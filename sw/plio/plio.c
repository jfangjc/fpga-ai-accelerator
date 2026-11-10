#define _POSIX_C_SOURCE 200809L
#include "plio.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
uint64_t plio_now_ns(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return (uint64_t)t.tv_sec * 1000000000 + t.tv_nsec;
}
static int readnum(const char *p, uint64_t *n) {
  FILE *f = fopen(p, "r");
  if (!f)
    return -1;
  char s[64];
  int ok = fgets(s, sizeof(s), f) != NULL;
  fclose(f);
  if (!ok) {
    errno = EIO;
    return -1;
  }
  *n = strtoull(s, NULL, 0);
  return 0;
}
int plio_uio_open(struct plio_uio *u, const char *name) {
  memset(u, 0, sizeof(*u));
  u->fd = -1;
  DIR *d = opendir("/sys/class/uio");
  if (!d)
    return -1;
  struct dirent *e;
  char path[256], s[128];
  int found = 0;
  while ((e = readdir(d))) {
    if (strncmp(e->d_name, "uio", 3))
      continue;
    snprintf(path, sizeof(path), "/sys/class/uio/%.32s/name", e->d_name);
    FILE *f = fopen(path, "r");
    if (!f)
      continue;
    s[0] = 0;
    if (!fgets(s, sizeof(s), f)) {
      fclose(f);
      continue;
    }
    fclose(f);
    s[strcspn(s, "\n")] = 0;
    if (strcmp(s, name))
      continue;
    snprintf(path, sizeof(path), "/sys/class/uio/%.32s/maps/map0/size",
             e->d_name);
    uint64_t n;
    if (readnum(path, &n))
      break;
    u->size = n;
    snprintf(path, sizeof(path), "/dev/%.32s", e->d_name);
    u->fd = open(path, O_RDWR);
    if (u->fd < 0)
      break;
    u->regs = mmap(NULL, u->size, PROT_READ | PROT_WRITE, MAP_SHARED, u->fd, 0);
    if (u->regs == MAP_FAILED) {
      close(u->fd);
      u->fd = -1;
      break;
    }
    found = 1;
    break;
  }
  closedir(d);
  if (!found) {
    errno = ENODEV;
    return -1;
  }
  return 0;
}
void plio_uio_close(struct plio_uio *u) {
  if (u->fd >= 0) {
    munmap((void *)u->regs, u->size);
    close(u->fd);
    u->fd = -1;
  }
}
int plio_irq_enable(struct plio_uio *u) {
  uint32_t one = 1;
  return write(u->fd, &one, 4) == 4 ? 0 : -1;
}
int plio_irq_wait(struct plio_uio *u, int ms, uint32_t *delta) {
  struct pollfd p = {u->fd, POLLIN, 0};
  int r;
  do {
    r = poll(&p, 1, ms);
  } while (r < 0 && errno == EINTR);
  if (r <= 0) {
    if (!r)
      errno = ETIMEDOUT;
    return -1;
  }
  uint32_t n;
  if (read(u->fd, &n, 4) != 4)
    return -1;
  if (delta)
    *delta = n - u->count;
  u->count = n;
  return 0;
}
int plio_buffer_open(struct plio_buffer *b, unsigned i) {
  memset(b, 0, sizeof(*b));
  b->fd = -1;
  snprintf(b->sysfs, sizeof(b->sysfs), "/sys/class/u-dma-buf/udmabuf%u", i);
  char p[256];
  uint64_t n;
  snprintf(p, sizeof(p), "%s/phys_addr", b->sysfs);
  if (readnum(p, &b->phys))
    return -1;
  snprintf(p, sizeof(p), "%s/size", b->sysfs);
  if (readnum(p, &n))
    return -1;
  b->size = n;
  snprintf(p, sizeof(p), "/dev/udmabuf%u", i);
  b->fd = open(p, O_RDWR);
  if (b->fd < 0)
    return -1;
  b->data = mmap(NULL, b->size, PROT_READ | PROT_WRITE, MAP_SHARED, b->fd, 0);
  if (b->data == MAP_FAILED) {
    close(b->fd);
    b->fd = -1;
    return -1;
  }
  return 0;
}
void plio_buffer_close(struct plio_buffer *b) {
  if (b->fd >= 0) {
    munmap(b->data, b->size);
    close(b->fd);
    b->fd = -1;
  }
}
static int syncbuf(struct plio_buffer *b, const char *attr) {
  char p[256];
  snprintf(p, sizeof(p), "%s/%s", b->sysfs, attr);
  atomic_thread_fence(memory_order_seq_cst);
  int fd = open(p, O_WRONLY);
  if (fd < 0)
    return -1;
  int r = write(fd, "1", 1) == 1 ? 0 : -1;
  close(fd);
  atomic_thread_fence(memory_order_seq_cst);
  return r;
}
static int setnum(struct plio_buffer *b, const char *attr, size_t value) {
  char p[256], s[32];
  snprintf(p, sizeof(p), "%s/%s", b->sysfs, attr);
  int n = snprintf(s, sizeof(s), "%zu", value);
  int fd = open(p, O_WRONLY);
  if (fd < 0)
    return -1;
  int r = write(fd, s, n) == n ? 0 : -1;
  close(fd);
  return r;
}
int plio_buffer_sync_range(struct plio_buffer *b, size_t off, size_t n,
                           int cpu) {
  if (!n || off > b->size || n > b->size - off || (off & 63) || (n & 63)) {
    errno = EINVAL;
    return -1;
  }
  if (setnum(b, "sync_offset", off) || setnum(b, "sync_size", n) ||
      setnum(b, "sync_direction", 0))
    return -1;
  return syncbuf(b, cpu ? "sync_for_cpu" : "sync_for_device");
}
int plio_buffer_device(struct plio_buffer *b) {
  return plio_buffer_sync_range(b, 0, b->size, 0);
}
int plio_buffer_cpu(struct plio_buffer *b) {
  return plio_buffer_sync_range(b, 0, b->size, 1);
}
static void wr(struct plio_dma *d, unsigned o, uint32_t v) {
  atomic_thread_fence(memory_order_seq_cst);
  d->tx.regs[o / 4] = v;
  atomic_thread_fence(memory_order_seq_cst);
}
static uint32_t rd(struct plio_dma *d, unsigned o) {
  uint32_t v = d->tx.regs[o / 4];
  atomic_thread_fence(memory_order_seq_cst);
  return v;
}
int plio_dma_open(struct plio_dma *d) {
  return plio_dma_open_named(d, "stt-dma-tx", "stt-dma-rx");
}
int plio_dma_open_named(struct plio_dma *d, const char *tx, const char *rx) {
  d->tx.fd = d->rx.fd = -1;
  if (plio_uio_open(&d->tx, tx))
    return -1;
  if (plio_uio_open(&d->rx, rx)) {
    plio_uio_close(&d->tx);
    return -1;
  }
  if (plio_dma_reset(d, 1000)) {
    plio_dma_close(d);
    return -1;
  }
  return 0;
}
void plio_dma_close(struct plio_dma *d) {
  plio_uio_close(&d->rx);
  plio_uio_close(&d->tx);
}
int plio_dma_reset(struct plio_dma *d, int ms) {
  wr(d, 0, 4);
  uint64_t end = plio_now_ns() + (uint64_t)ms * 1000000;
  while (rd(d, 0) & 4) {
    if (plio_now_ns() > end) {
      errno = ETIMEDOUT;
      return -1;
    }
  }
  wr(d, 4, 0x7000);
  wr(d, 0x34, 0x7000);
  return 0;
}
int plio_dma_start(struct plio_dma *d, uint64_t src, uint64_t dst, size_t n) {
  return plio_dma_start_sized(d, src, n, dst, n);
}
int plio_dma_start_sized(struct plio_dma *d, uint64_t src, size_t n,
                         uint64_t dst, size_t capacity) {
  if (!n || n >= ((size_t)1 << 26) || !capacity ||
      capacity >= ((size_t)1 << 26)) {
    errno = EINVAL;
    return -1;
  }
  wr(d, 4, 0x7000);
  wr(d, 0x34, 0x7000);
  if (plio_irq_enable(&d->rx) || plio_irq_enable(&d->tx))
    return -1;
  wr(d, 0x30, 0x5001);
  wr(d, 0x48, (uint32_t)dst);
  wr(d, 0x4c, dst >> 32);
  wr(d, 0x58, capacity);
  wr(d, 0, 0x5001);
  wr(d, 0x18, (uint32_t)src);
  wr(d, 0x1c, src >> 32);
  wr(d, 0x28, n);
  return 0;
}
size_t plio_dma_received_bytes(struct plio_dma *d) { return rd(d, 0x58); }
int plio_dma_wait(struct plio_dma *d, int ms) {
  uint64_t end = plio_now_ns() + (uint64_t)ms * 1000000;
  struct plio_uio *u[2] = {&d->tx, &d->rx};
  for (int i = 0; i < 2; i++) {
    unsigned off = i ? 0x34 : 4;
    for (;;) {
      uint32_t s = rd(d, off);
      if (s & 0x4070) {
        errno = EIO;
        goto fail;
      }
      if (s & 0x1000) {
        wr(d, off, 0x7000);
        break;
      }
      uint64_t now = plio_now_ns();
      if (now >= end) {
        errno = ETIMEDOUT;
        goto fail;
      }
      /* A previously completed transfer may leave an unread UIO count.
       * Re-arm after consuming such a wakeup before waiting again. */
      if (plio_irq_enable(u[i]) ||
          plio_irq_wait(u[i], (int)((end - now) / 1000000 + 1), NULL))
        goto fail;
    }
  }
  return 0;
fail: {
  int saved = errno;
  plio_dma_reset(d, 1000);
  errno = saved;
  return -1;
}
}
