#include "../plio/plio.h"
#include "transport.h"
#include <errno.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
struct board {
  struct plio_dma dma;
  struct plio_uio regs;
  struct plio_buffer tx, rx;
  int ms, sg;
  /* CPU->device TX range [tx_base, tx_base + tx_owned); RX from 0. */
  size_t tx_base, tx_owned, rx_owned;
  /* Resident RUN_NETWORK program, device-owned (clean) once loaded. */
  size_t prog_off, prog_bytes, limit;
};
/* SG descriptors plus the largest network header+features segment. */
#define NETWORK_HEAD (128 + 64 + 64 * (size_t)ENGDRV_NETWORK_FRAMES)
static void wr(struct board *b, unsigned o, uint32_t v) {
  atomic_thread_fence(memory_order_seq_cst);
  b->regs.regs[o / 4] = v;
  atomic_thread_fence(memory_order_seq_cst);
}
static int reset(struct transport *t) {
  struct board *b = t->state;
  wr(b, 0, 1);
  int r = plio_dma_reset(&b->dma, b->ms);
  /* MM2S can deliver buffered beats after the first engine reset. Reset the
   * parser again only after both DMA channels have stopped, so the next
   * command cannot inherit the aborted RUN_NETWORK packet tail. */
  wr(b, 0, 1);
  if (!r && b->tx_owned) {
    if (plio_buffer_sync_range(&b->tx, b->tx_base, b->tx_owned, 1))
      r = -1;
    else
      b->tx_owned = 0;
  }
  /* Network SG descriptors live outside the tracked packet range. */
  if (!r && b->sg && b->prog_bytes &&
      plio_buffer_sync_range(&b->tx, 0, 128, 1))
    r = -1;
  if (!r && b->rx_owned) {
    if (plio_buffer_sync_range(&b->rx, 0, b->rx_owned, 1))
      r = -1;
    else
      b->rx_owned = 0;
  }
  wr(b, 0x10, 3);
  wr(b, 0x0c, 3);
  return r;
}
static uint32_t version(struct transport *t) {
  struct board *b = t->state;
  return b->regs.regs[2];
}
static void destroy(struct transport *t) {
  struct board *b = t->state;
  if (b->regs.fd >= 0 && b->dma.tx.fd >= 0 && b->dma.rx.fd >= 0 &&
      b->regs.size >= 0x3c)
    reset(t);
  plio_buffer_close(&b->tx);
  plio_buffer_close(&b->rx);
  plio_dma_close(&b->dma);
  plio_uio_close(&b->regs);
  free(b);
  free(t);
}
static void dmawr(struct board *b, unsigned off, uint32_t v) {
  atomic_thread_fence(memory_order_seq_cst);
  b->dma.tx.regs[off / 4] = v;
  atomic_thread_fence(memory_order_seq_cst);
}
static void dma64(struct board *b, unsigned off, uint64_t v) {
  dmawr(b, off, (uint32_t)v);
  dmawr(b, off + 4, (uint32_t)(v >> 32));
}
/* SG descriptor at byte offset `at` of buffer b. */
static void descriptor(struct plio_buffer *b, size_t at, size_t next,
                       size_t data, size_t bytes, uint32_t flags) {
  uint32_t *d = (uint32_t *)((char *)b->data + at);
  memset(d, 0, 64);
  d[0] = (uint32_t)(b->phys + next);
  d[1] = (b->phys + next) >> 32;
  d[2] = (uint32_t)(b->phys + data);
  d[3] = (b->phys + data) >> 32;
  d[6] = (uint32_t)bytes | flags;
}
#define SOF (1u << 27)
#define EOF_ (1u << 26)
static int sync(struct transport *t, struct plio_buffer *buf, size_t off,
                size_t n, int cpu) {
  uint64_t start = plio_now_ns();
  int r = plio_buffer_sync_range(buf, off, n, cpu);
  t->sync_ns += plio_now_ns() - start;
  t->sync_calls++;
  return r;
}
/* One deadline across both channels; handle DMA and SG master errors. */
static int wait_dma(struct board *b) {
  uint64_t end = plio_now_ns() + (uint64_t)b->ms * 1000000;
  struct plio_uio *u[2] = {&b->dma.tx, &b->dma.rx};
  for (unsigned i = 0; i < 2; i++)
    for (;;) {
      unsigned off = i ? 0x34 : 4;
      uint32_t status = b->dma.tx.regs[off / 4];
      if (status & 0x770) {
        errno = (status & 0x440) ? ENXIO : (status & 0x220) ? EFAULT : EIO;
        return -1;
      }
      if (status & 0x1000) {
        dmawr(b, off, 0x7000);
        break;
      }
      uint64_t now = plio_now_ns();
      if (now >= end) {
        errno = ETIMEDOUT;
        return -1;
      }
      if (plio_irq_enable(u[i]) ||
          plio_irq_wait(u[i], (int)((end - now) / 1000000 + 1), NULL))
        return -1;
    }
  return 0;
}
/* Shared transfer. Per-layer (seg 0): packet at TX offset `offset`.
 * Network (prog != 0): packet ends at prog_off and is followed by the
 * resident program, so only the header+features range is cleaned. */
static int transfer(struct transport *t, const void *p, size_t n, void *r,
                    size_t *cap, struct engdrv_timing *tim, int network) {
  struct board *b = t->state;
  size_t offset = b->sg ? 64 : 0, tx = (n + 63) & ~(size_t)63,
         rx = (*cap + 63) & ~(size_t)63, base;
  size_t total = network ? n + b->prog_bytes : n;
  if (network) {
    if (!b->prog_bytes) {
      errno = ENOTSUP;
      return -1;
    }
    if (n % 64 || n + 128 > b->prog_off) {
      errno = ENOSPC;
      return -1;
    }
    base = b->prog_off - n;
  } else {
    if (tx + offset > b->limit) {
      errno = ENOSPC;
      return -1;
    }
    base = offset;
  }
  if (rx + offset > b->rx.size || total >= 1u << 26 || *cap >= 1u << 26) {
    errno = ENOSPC;
    return -1;
  }
  if (b->sg && plio_dma_reset(&b->dma, b->ms))
    return -1;
  memcpy((char *)b->tx.data + base, p, n);
  if (!network)
    memset((char *)b->tx.data + base + n, 0, tx - n);
  wr(b, 0x10, 3);
  if (b->sg) {
    if (network) {
      descriptor(&b->tx, 0, 64, base, n, SOF);
      descriptor(&b->tx, 64, 64, b->prog_off, b->prog_bytes, EOF_);
    } else
      descriptor(&b->tx, 0, 0, 64, n, SOF | EOF_);
    descriptor(&b->rx, 0, 0, 64, *cap, 0);
  }
  /* Descriptors (SG) and the written packet range only. */
  if (b->sg && network && sync(t, &b->tx, 0, 128, 0))
    return -1;
  size_t lo = network ? base : 0, len = network ? n : tx + offset;
  if (sync(t, &b->tx, lo, len, 0))
    return -1;
  b->tx_base = lo;
  b->tx_owned = len;
  if (sync(t, &b->rx, 0, rx + offset, 0))
    return -1;
  b->rx_owned = rx + offset;
  if (b->sg) {
    dma64(b, 0x38, b->rx.phys);
    dma64(b, 8, b->tx.phys);
    if (plio_irq_enable(&b->dma.rx) || plio_irq_enable(&b->dma.tx))
      return -1;
    dmawr(b, 0x30, 0x5001);
    dmawr(b, 0, 0x5001);
    /* TAILDESC LSB before MSB, which triggers descriptor fetch. RX first. */
    dma64(b, 0x40, b->rx.phys);
    dma64(b, 0x10, b->tx.phys + (network ? 64 : 0));
  } else if (plio_dma_start_sized(&b->dma, b->tx.phys + base, total,
                                  b->rx.phys, *cap))
    return -1;
  if (wait_dma(b))
    return -1;
  tim->irq_ns = plio_now_ns();
  if (sync(t, &b->rx, 0, rx + offset, 1))
    return -1;
  b->rx_owned = 0;
  if (sync(t, &b->tx, b->tx_base, b->tx_owned, 1) ||
      (b->sg && network && sync(t, &b->tx, 0, 128, 1)))
    return -1;
  b->tx_owned = 0;
  size_t got;
  if (b->sg) {
    uint32_t ts = ((uint32_t *)b->tx.data)[7], rs = ((uint32_t *)b->rx.data)[7];
    uint32_t t1 = network ? ((uint32_t *)b->tx.data)[16 + 7] : ts;
    if (!(ts >> 31) || !(t1 >> 31) || !(rs >> 31) || (ts & 0x70000000) ||
        (t1 & 0x70000000) || (rs & 0x70000000)) {
      errno = EIO;
      return -1;
    }
    got = rs & 0x3ffffff;
  } else
    got = plio_dma_received_bytes(&b->dma);
  if (got > *cap) {
    errno = EPROTO;
    return -1;
  }
  memcpy(r, (char *)b->rx.data + offset, got);
  *cap = got;
  wr(b, 0x10, 3);
  return 0;
}
static int exchange(struct transport *t, const void *p, size_t n, void *r,
                    size_t *cap, struct engdrv_timing *tim) {
  return transfer(t, p, n, r, cap, tim, 0);
}
static int network(struct transport *t, const void *p, size_t n, void *r,
                   size_t *cap, struct engdrv_timing *tim) {
  return transfer(t, p, n, r, cap, tim, 1);
}
/* Copy the immutable program to the end of TX and clean it once. Per-layer
 * packets are then limited to the space below the network header area. */
static int program(struct transport *t, const void *p, size_t n) {
  struct board *b = t->state;
  size_t need = (n + 63) & ~(size_t)63;
  if (b->prog_bytes) {
    if (sync(t, &b->tx, b->prog_off, (b->prog_bytes + 63) & ~(size_t)63, 1))
      return -1;
    b->prog_bytes = b->prog_off = 0;
    b->limit = b->tx.size;
  }
  if (n % 16 || need + NETWORK_HEAD > b->tx.size || n >= 1u << 26) {
    errno = n % 16 ? EINVAL : ENOSPC;
    return -1;
  }
  size_t off = (b->tx.size - need) & ~(size_t)4095;
  memcpy((char *)b->tx.data + off, p, n);
  memset((char *)b->tx.data + off + n, 0, need - n);
  if (sync(t, &b->tx, off, need, 0))
    return -1;
  b->prog_off = off;
  b->prog_bytes = n;
  b->limit = (off - (64 + 64 * (size_t)ENGDRV_NETWORK_FRAMES)) & ~(size_t)63;
  return 0;
}
int board_open(struct transport **out, const struct engdrv_config *cfg) {
  struct transport *t = calloc(1, sizeof(*t));
  struct board *b = calloc(1, sizeof(*b));
  if (!t || !b) {
    free(t);
    free(b);
    return -1;
  }
  t->state = b;
  t->exchange = exchange;
  t->reset = reset;
  t->version = version;
  t->close = destroy;
  t->program = program;
  t->network = network;
  b->ms = cfg->timeout_ms;
  b->regs.fd = b->dma.tx.fd = b->dma.rx.fd = b->tx.fd = b->rx.fd = -1;
  if (cfg->tx_buffer == cfg->rx_buffer) {
    errno = EINVAL;
    goto fail;
  }
  if (plio_uio_open(&b->regs, "stt-engine") ||
      plio_dma_open_named(&b->dma, "stt-engine-tx", "stt-engine-rx") ||
      plio_buffer_open(&b->tx, cfg->tx_buffer) ||
      plio_buffer_open(&b->rx, cfg->rx_buffer))
    goto fail;
  if (b->regs.size < 0x3c) {
    errno = ENODEV;
    goto fail;
  }
  if (reset(t))
    goto fail;
  b->sg = !!(b->dma.tx.regs[1] & 8);
  b->limit = b->tx.size;
  *out = t;
  return 0;
fail: {
  int err = errno;
  destroy(t);
  errno = err;
  return -1;
}
}
