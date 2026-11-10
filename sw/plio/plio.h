#ifndef PLIO_H
#define PLIO_H
#include <stddef.h>
#include <stdint.h>
struct plio_uio {
  int fd;
  volatile uint32_t *regs;
  size_t size;
  uint32_t count;
};
struct plio_buffer {
  int fd;
  void *data;
  uint64_t phys;
  size_t size;
  char sysfs[128];
};
struct plio_dma {
  struct plio_uio tx, rx;
};
uint64_t plio_now_ns(void);
int plio_uio_open(struct plio_uio *, const char *name);
void plio_uio_close(struct plio_uio *);
int plio_irq_enable(struct plio_uio *);
int plio_irq_wait(struct plio_uio *, int timeout_ms, uint32_t *delta);
int plio_buffer_open(struct plio_buffer *, unsigned index);
void plio_buffer_close(struct plio_buffer *);
/* Whole buffer sync; exclusively owned from device-sync until CPU-sync. */
int plio_buffer_device(struct plio_buffer *);
int plio_buffer_cpu(struct plio_buffer *);
/* Both offset and size must be multiples of 64 bytes. Single owner per buffer.
 * for_cpu=0 hands the range to PL; for_cpu=1 returns it to CPU. */
int plio_buffer_sync_range(struct plio_buffer *, size_t offset, size_t size,
                           int for_cpu);
int plio_dma_open(struct plio_dma *);
int plio_dma_open_named(struct plio_dma *, const char *tx_name,
                        const char *rx_name);
void plio_dma_close(struct plio_dma *);
int plio_dma_reset(struct plio_dma *, int timeout_ms);
/* Start RX before TX. length is 1..(2^26-1), addresses are byte addressed. */
int plio_dma_start(struct plio_dma *, uint64_t src, uint64_t dst,
                   size_t length);
int plio_dma_start_sized(struct plio_dma *, uint64_t src, size_t src_bytes,
                         uint64_t dst, size_t dst_capacity);
size_t plio_dma_received_bytes(struct plio_dma *);
int plio_dma_wait(struct plio_dma *, int timeout_ms);
#endif
