#define _POSIX_C_SOURCE 200809L
#include "fedrv.h"
#include <errno.h>
#include <string.h>
#include <time.h>

/* stt_frontend registers (docs/frontend_spec.md) */
enum { CONTROL = 0x00, STATUS = 0x04, CLEAR = 0x08, IRQ_PERIOD = 0x0c, FRAMES = 0x10,
       OVERRUNS = 0x14, BEAM = 0x20, MIC1 = 0x40, MIC2 = 0x50 };
enum { C_ENABLE = 1, C_PACE = 8, C_RIGHT = 32, C_LED = 64, C_BUTTON_IRQ = 128 };
/* AXI DMA scatter-gather registers (PG021) */
enum { MM2S_CR = 0x00, MM2S_SR = 0x04, MM2S_CUR = 0x08, MM2S_TAIL = 0x10,
       S2MM_CR = 0x30, S2MM_SR = 0x34, S2MM_CUR = 0x38, S2MM_TAIL = 0x40 };
enum { CR_RS = 1, CR_CYCLIC = 16, CR_IOC = 1 << 12, CR_ERR = 1 << 14,
       SR_IRQS = 0x7000, SR_ERRORS = 0x0770 };
/* 64-bit descriptor: next, buffer, control (length | SOF/EOF), status */
enum { BD_NEXT = 0x00, BD_BUF = 0x08, BD_CONTROL = 0x18, BD_STATUS = 0x1c, BD_SIZE = 64 };
enum { BD_SOF = 1u << 27, BD_EOF = 1u << 26, BD_CMPLT = 1u << 31, BD_ERRORS = 7u << 28,
       BD_LENGTH = (1u << 26) - 1 };
/* Buffer layout: descriptors first, data from 64 KiB. */
#define DATA_OFFSET 0x10000u
#define RX_STRIDE 576u /* 520-byte packet rounded to cache lines */
#define TX_STRIDE (FE_TX_CHUNK * 4u)

static void put(volatile uint32_t *r, unsigned o, uint32_t v) {
  atomic_thread_fence(memory_order_seq_cst);
  r[o / 4] = v;
#ifdef FE_TEST_MMIO
  fe_test_mmio(o, v);
#endif
  atomic_thread_fence(memory_order_seq_cst);
}
static uint32_t get(volatile uint32_t *r, unsigned o) {
  uint32_t v = r[o / 4];
  atomic_thread_fence(memory_order_seq_cst);
  return v;
}
static volatile uint32_t *dma(struct fe *f) { return f->dma.tx.regs; }
static void put64(struct fe *f, unsigned o, uint64_t v) {
  put(dma(f), o, (uint32_t)v);
  /* PG021: with 64-bit addressing TAILDESC_MSB triggers the fetch. */
  put(dma(f), o + 4, (uint32_t)(v >> 32));
}
static void sleep_us(long us) {
  struct timespec t = {us / 1000000, (us % 1000000) * 1000};
  while (nanosleep(&t, &t) && errno == EINTR) {}
}
/* Cache maintenance on [first, first+count) slots of a ring, wrapping. */
static int sync_ring(struct plio_buffer *b, size_t base, size_t stride, unsigned slots,
                     unsigned first, unsigned count, int cpu) {
  while (count) {
    unsigned i = first % slots, n = count < slots - i ? count : slots - i;
    size_t off = base + (size_t)i * stride, len = ((size_t)n * stride + 63) & ~(size_t)63;
    if (plio_buffer_sync_range(b, off, len, cpu)) return -1;
    first += n;
    count -= n;
  }
  return 0;
}
static uint32_t *bd(struct plio_buffer *b, unsigned i) {
  return (uint32_t *)((char *)b->data + (size_t)i * BD_SIZE);
}
static void bd_write(uint32_t *d, uint64_t next, uint64_t buf, uint32_t control) {
  memset(d, 0, BD_SIZE);
  d[BD_NEXT / 4] = (uint32_t)next;
  d[BD_NEXT / 4 + 1] = (uint32_t)(next >> 32);
  d[BD_BUF / 4] = (uint32_t)buf;
  d[BD_BUF / 4 + 1] = (uint32_t)(buf >> 32);
  d[BD_CONTROL / 4] = control;
}

void fe_stop(struct fe *f) {
  f->control &= ~(uint32_t)C_ENABLE;
  put(f->regs.regs, CONTROL, f->control);
  f->running = 0;
}

int fe_reset(struct fe *f) {
  uint32_t led = f->control & C_LED;
  fe_stop(f);
  if (plio_dma_reset(&f->dma, 1000)) return -1;
  put(f->reset.regs, 0, 1); /* active-high front-end-only reset (GPIO channel 1) */
  sleep_us(100);
  put(f->reset.regs, 0, 0);
  sleep_us(100);
  f->control = led;
  put(f->regs.regs, CONTROL, f->control);
  put(f->regs.regs, CLEAR, 0xf);
  /* S2MM: circular chain of one-frame descriptors over fixed slots. */
  for (unsigned i = 0; i < FE_RX_SLOTS; i++)
    bd_write(bd(&f->rx, i), f->rx.phys + (uint64_t)((i + 1) % FE_RX_SLOTS) * BD_SIZE,
             f->rx.phys + DATA_OFFSET + (uint64_t)i * RX_STRIDE, RX_STRIDE);
  /* Mark slots so stale data can never pass as the expected frame. */
  memset((char *)f->rx.data + DATA_OFFSET, 0xff, (size_t)FE_RX_SLOTS * RX_STRIDE);
  for (unsigned i = 0; i < FE_TX_SLOTS; i++)
    bd_write(bd(&f->tx, i), f->tx.phys + (uint64_t)((i + 1) % FE_TX_SLOTS) * BD_SIZE,
             f->tx.phys + DATA_OFFSET + (uint64_t)i * TX_STRIDE, 0);
  if (plio_buffer_sync_range(&f->rx, 0, DATA_OFFSET + (size_t)FE_RX_SLOTS * RX_STRIDE, 0) ||
      plio_buffer_sync_range(&f->tx, 0, (size_t)FE_TX_SLOTS * BD_SIZE, 0))
    return -1;
  f->tx_head = f->tx_tail = 0;
  f->tx_samples = f->tx_start_ns = 0;
  atomic_store(&f->tx_submitted, 0);
  f->rx_pos = 0;
  f->rx_consumed = f->next_sequence = 0;
  memset(&f->stats, 0, sizeof(f->stats));
  /* Both channels halted after reset: program the ring heads now. */
  put64(f, S2MM_CUR, f->rx.phys);
  put64(f, MM2S_CUR, f->tx.phys);
  return 0;
}

void fe_close(struct fe *f) {
  if (f->regs.fd >= 0) {
    fe_stop(f);
  }
  if (f->dma.tx.fd >= 0 && f->dma.rx.fd >= 0)
    plio_dma_reset(&f->dma, 1000);
  plio_buffer_close(&f->rx);
  plio_buffer_close(&f->tx);
  plio_dma_close(&f->dma);
  plio_uio_close(&f->irq);
  plio_uio_close(&f->reset);
  plio_uio_close(&f->regs);
}

int fe_open(struct fe *f) {
  memset(f, 0, sizeof(*f));
  f->regs.fd = f->reset.fd = f->irq.fd = f->dma.tx.fd = f->dma.rx.fd = f->tx.fd = f->rx.fd = -1;
  /* The stt-frontend endpoint maps the registers; a second open of the same
   * UIO gives the button/frame IRQ its own file descriptor. */
  if (plio_uio_open(&f->regs, "stt-frontend") || plio_uio_open(&f->irq, "stt-frontend") ||
      plio_uio_open(&f->reset, "stt-fe-reset") ||
      plio_dma_open_named(&f->dma, "stt-fe-tx", "stt-fe-rx") || plio_buffer_open(&f->tx, 0) ||
      plio_buffer_open(&f->rx, 1))
    goto fail;
  if (f->tx.size < DATA_OFFSET + (size_t)FE_TX_SLOTS * TX_STRIDE ||
      f->rx.size < DATA_OFFSET + (size_t)FE_RX_SLOTS * RX_STRIDE) {
    errno = ENOMEM;
    goto fail;
  }
  if (fe_reset(f)) goto fail;
  return 0;
fail: {
  int e = errno;
  fe_close(f);
  errno = e;
  return -1;
}
}

int fe_configure(struct fe *f, const struct fe_config *c) {
  if (f->running || (unsigned)c->source > 3 || c->irq_frames < 1 || c->irq_frames > 255) {
    errno = EINVAL;
    return -1;
  }
  f->config = *c;
  f->control = (f->control & C_LED) | ((uint32_t)c->source << 1) | (c->paced ? C_PACE : 0) |
               (c->right_channel ? C_RIGHT : 0) | (c->button_irq ? C_BUTTON_IRQ : 0);
  put(f->regs.regs, CONTROL, f->control);
  put(f->regs.regs, IRQ_PERIOD, c->irq_frames);
  put(f->regs.regs, BEAM, c->delay1 | (uint32_t)c->delay2 << 8 | (uint32_t)(uint16_t)c->gain << 16);
  return 0;
}

int fe_start(struct fe *f) {
  if (f->running) {
    errno = EBUSY;
    return -1;
  }
  if (get(dma(f), S2MM_SR) & 1) { /* halted: (re)start the cyclic ring */
    put(dma(f), S2MM_SR, SR_IRQS);
    put(dma(f), S2MM_CR, CR_RS | CR_CYCLIC | CR_IOC | CR_ERR | f->config.irq_frames << 16);
    /* Cyclic mode: any tail outside the chain starts the engine. */
    put64(f, S2MM_TAIL, f->rx.phys + (uint64_t)FE_RX_SLOTS * BD_SIZE);
    put(dma(f), MM2S_SR, SR_IRQS);
    put(dma(f), MM2S_CR, CR_RS | CR_IOC | CR_ERR | 1u << 16);
  }
  if (plio_irq_enable(&f->dma.rx) || plio_irq_enable(&f->dma.tx)) return -1;
  f->control |= C_ENABLE;
  put(f->regs.regs, CONTROL, f->control);
  f->running = 1;
  return 0;
}

/* Retire completed MM2S descriptors. */
static int tx_reclaim(struct fe *f) {
  unsigned n = f->tx_head - f->tx_tail;
  if (!n) return 0;
  if (sync_ring(&f->tx, 0, BD_SIZE, FE_TX_SLOTS, f->tx_tail, n, 1)) return -1;
  while (f->tx_tail != f->tx_head) {
    uint32_t s = bd(&f->tx, f->tx_tail % FE_TX_SLOTS)[BD_STATUS / 4];
    if (s & BD_ERRORS) {
      errno = EIO;
      return -1;
    }
    if (!(s & BD_CMPLT)) break;
    f->tx_tail++;
  }
  if (get(dma(f), MM2S_SR) & SR_ERRORS) {
    errno = EIO;
    return -1;
  }
  return 0;
}

long fe_inject(struct fe *f, const int32_t *x, size_t n, int ms) {
  size_t done = 0;
  uint64_t deadline = plio_now_ns() + (uint64_t)ms * 1000000;
  while (done < n) {
    if (tx_reclaim(f)) return -1;
    unsigned room = FE_TX_SLOTS - 1 - (f->tx_head - f->tx_tail);
    if (!room) {
      /* Clear, arm, re-check, then sleep on the MM2S IRQ. */
      put(dma(f), MM2S_SR, SR_IRQS);
      if (plio_irq_enable(&f->dma.tx) || tx_reclaim(f)) return -1;
      if (f->tx_head - f->tx_tail < FE_TX_SLOTS - 1) continue;
      uint64_t now = plio_now_ns();
      if (now >= deadline) {
        errno = ETIMEDOUT;
        return -1;
      }
      if (plio_irq_wait(&f->dma.tx, (int)((deadline - now) / 1000000) + 1, NULL) &&
          errno != ETIMEDOUT)
        return -1;
      continue;
    }
    if (f->config.paced && f->tx_head && f->tx_head == f->tx_tail) f->stats.tx_underruns++;
    unsigned first = f->tx_head, k = 0;
    while (k < room && done < n) {
      size_t m = n - done < FE_TX_CHUNK ? n - done : FE_TX_CHUNK;
      unsigned i = (first + k) % FE_TX_SLOTS;
      memcpy((char *)f->tx.data + DATA_OFFSET + (size_t)i * TX_STRIDE, x + done, m * 4);
      uint32_t *d = bd(&f->tx, i);
      d[BD_CONTROL / 4] = (uint32_t)(m * 4) | BD_SOF | BD_EOF;
      d[BD_STATUS / 4] = 0;
      done += m;
      k++;
      f->tx_samples += m;
    }
    if (sync_ring(&f->tx, DATA_OFFSET, TX_STRIDE, FE_TX_SLOTS, first, k, 0) ||
        sync_ring(&f->tx, 0, BD_SIZE, FE_TX_SLOTS, first, k, 0))
      return -1;
    f->tx_head = first + k;
    if (!f->tx_start_ns) f->tx_start_ns = plio_now_ns();
    put64(f, MM2S_TAIL, f->tx.phys + (uint64_t)((f->tx_head - 1) % FE_TX_SLOTS) * BD_SIZE);
    atomic_store(&f->tx_submitted, f->tx_samples);
  }
  return (long)done;
}

int fe_inject_drain(struct fe *f, int ms) {
  uint64_t deadline = plio_now_ns() + (uint64_t)ms * 1000000;
  for (;;) {
    put(dma(f), MM2S_SR, SR_IRQS);
    if (plio_irq_enable(&f->dma.tx) || tx_reclaim(f)) return -1;
    if (f->tx_head == f->tx_tail) return 0;
    uint64_t now = plio_now_ns();
    if (now >= deadline) {
      errno = ETIMEDOUT;
      return -1;
    }
    if (plio_irq_wait(&f->dma.tx, (int)((deadline - now) / 1000000) + 1, NULL) &&
        errno != ETIMEDOUT)
      return -1;
  }
}

uint32_t fe_hw_frames(struct fe *f) { return get(f->regs.regs, FRAMES); }

/* Copy out completed frames without waiting. */
static int rx_collect(struct fe *f, struct fe_frame *out, size_t max, uint64_t irq_ns) {
  if (get(dma(f), S2MM_SR) & SR_ERRORS) {
    errno = EIO;
    return -1;
  }
  uint32_t avail = fe_hw_frames(f) - f->rx_consumed;
  if (!avail) return 0;
  if (avail >= FE_RX_SLOTS) { /* the DMA has overwritten unread slots */
    f->stats.ring_overruns += avail - (FE_RX_SLOTS - 1);
    errno = EOVERFLOW;
    return -1;
  }
  unsigned n = avail < max ? avail : (unsigned)max, c = 0;
  /* FRAMES counts packets accepted by the stream; completion is the
   * descriptor status written after the data. Read status, then data. */
  if (sync_ring(&f->rx, 0, BD_SIZE, FE_RX_SLOTS, f->rx_pos, n, 1)) return -1;
  while (c < n) {
    uint32_t s = bd(&f->rx, (f->rx_pos + c) % FE_RX_SLOTS)[BD_STATUS / 4];
    if (s & BD_ERRORS) {
      errno = EIO;
      return -1;
    }
    if (!(s & BD_CMPLT)) break;
    if ((s & BD_LENGTH) != FE_PACKET_BYTES) {
      f->stats.sequence_errors++;
      errno = EPROTO;
      return -1;
    }
    c++;
  }
  if (!c) return 0;
  if (sync_ring(&f->rx, DATA_OFFSET, RX_STRIDE, FE_RX_SLOTS, f->rx_pos, c, 1)) return -1;
  uint64_t now = plio_now_ns();
  for (unsigned j = 0; j < c; j++) {
    unsigned i = (f->rx_pos + j) % FE_RX_SLOTS;
    const uint32_t *p = (const uint32_t *)((char *)f->rx.data + DATA_OFFSET + (size_t)i * RX_STRIDE);
    if (p[0] != f->next_sequence || (int32_t)p[1] != FE_EXPONENT) {
      f->stats.sequence_errors++;
      errno = EPROTO;
      return -1;
    }
    out[j].sequence = p[0];
    for (unsigned b = 0; b < FE_BANDS; b++)
      out[j].power[b] = p[2 + 2 * b] | (uint64_t)p[3 + 2 * b] << 32;
    out[j].irq_ns = irq_ns;
    out[j].avail_ns = now;
    f->next_sequence++;
    bd(&f->rx, i)[BD_STATUS / 4] = 0; /* cyclic mode ignores Cmplt; clear for the next lap */
  }
  if (sync_ring(&f->rx, 0, BD_SIZE, FE_RX_SLOTS, f->rx_pos, c, 0)) return -1;
  f->rx_pos = (f->rx_pos + c) % FE_RX_SLOTS;
  f->rx_consumed += c;
  f->stats.frames += c;
  f->stats.status = get(f->regs.regs, STATUS);
  f->stats.mic_overruns = get(f->regs.regs, OVERRUNS);
  return (int)c;
}

int fe_capture(struct fe *f, struct fe_frame *out, size_t max, int ms) {
  if (!out || !max || ms < 0) {
    errno = EINVAL;
    return -1;
  }
  uint64_t deadline = plio_now_ns() + (uint64_t)ms * 1000000, woke = plio_now_ns();
  for (;;) {
    int n = rx_collect(f, out, max, woke);
    if (n) return n;
    put(dma(f), S2MM_SR, SR_IRQS); /* clear the level source, arm, re-check */
    if (plio_irq_enable(&f->dma.rx)) return -1;
    if ((n = rx_collect(f, out, max, woke))) return n;
    uint64_t now = plio_now_ns();
    if (now >= deadline) return 0;
    if (plio_irq_wait(&f->dma.rx, (int)((deadline - now + 999999) / 1000000), NULL)) {
      if (errno == ETIMEDOUT) return rx_collect(f, out, max, plio_now_ns());
      return -1;
    }
    woke = plio_now_ns();
    f->stats.irqs++;
  }
}

void fe_led(struct fe *f, int on) {
  f->control = (f->control & ~(uint32_t)C_LED) | (on ? C_LED : 0);
  put(f->regs.regs, CONTROL, f->control);
}
int fe_ui_poll(struct fe *f, int speaking) {
  uint32_t control = (f->control & ~(uint32_t)C_LED) | (speaking ? C_LED : 0);
  if (control != f->control) {
    f->control = control;
    put(f->regs.regs, CONTROL, control);
  }
  if (!(get(f->regs.regs, STATUS) & 8)) return 0;
  put(f->regs.regs, CLEAR, 8);
  return 1;
}
uint32_t fe_status(struct fe *f) { return f->stats.status = get(f->regs.regs, STATUS); }
void fe_clear(struct fe *f, uint32_t bits) { put(f->regs.regs, CLEAR, bits & 0xf); }
int fe_button(struct fe *f) { return !!(fe_status(f) & 8); }
int fe_button_wait(struct fe *f, int ms) {
  uint64_t deadline = plio_now_ns() + (uint64_t)ms * 1000000;
  fe_clear(f, 9); /* frame IRQ shares the line: clear it with the button flag */
  for (;;) {
    if (plio_irq_enable(&f->irq)) return -1;
    uint64_t now = plio_now_ns();
    if (now >= deadline) return 0;
    if (plio_irq_wait(&f->irq, (int)((deadline - now) / 1000000) + 1, NULL))
      return errno == ETIMEDOUT ? 0 : -1;
    uint32_t s = fe_status(f);
    fe_clear(f, s & 9);
    if (s & 8) return 1;
  }
}
void fe_mic_read(struct fe *f, unsigned mic, struct fe_mic *m) {
  unsigned o = mic == 2 ? MIC2 : MIC1;
  m->peak = get(f->regs.regs, o);
  m->dc = (int32_t)get(f->regs.regs, o + 4);
  m->mean_square = get(f->regs.regs, o + 8) | (uint64_t)get(f->regs.regs, o + 12) << 32;
}
