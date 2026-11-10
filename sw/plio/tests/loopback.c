#include "plio.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define CHECK(x)                                                               \
  do {                                                                         \
    if ((x) < 0) {                                                             \
      perror(#x);                                                              \
      return 1;                                                                \
    }                                                                          \
  } while (0)
int main(void) {
  struct plio_buffer a, b;
  struct plio_dma d;
  CHECK(plio_buffer_open(&a, 0));
  CHECK(plio_buffer_open(&b, 1));
  CHECK(plio_dma_open(&d));
  size_t sizes[] = {16,   17,    31,      127,     4095,
                    4096, 65537, 1048576, 8388607, 8388608};
  for (unsigned t = 0; t < sizeof(sizes) / sizeof(*sizes); t++) {
    size_t n = sizes[t];
    for (size_t i = 0; i < n; i++)
      ((unsigned char *)a.data)[i] = (i * 73 + t * 19) ^ (i >> 8);
    memset(b.data, 0, n + 16);
    CHECK(plio_buffer_device(&a));
    CHECK(plio_buffer_device(&b));
    uint64_t start = plio_now_ns();
    CHECK(plio_dma_start(&d, a.phys, b.phys, n));
    CHECK(plio_dma_wait(&d, 5000));
    double sec = (plio_now_ns() - start) * 1e-9;
    CHECK(plio_buffer_cpu(&b));
    CHECK(plio_buffer_cpu(&a));
    if (memcmp(a.data, b.data, n)) {
      fprintf(stderr, "MISMATCH %zu\n", n);
      return 1;
    }
    for (size_t i = n; i < n + 16; i++)
      if (((unsigned char *)b.data)[i])
        return 1;
    printf("PASS bytes=%zu elapsed_s=%.9f per_direction_GBps=%.6f "
           "concurrent_GBps=%.6f\n",
           n, sec, n / sec / 1e9, 2 * n / sec / 1e9);
  }
  /* Reuse unchanged device-owned buffers to separate DMA rate from CPU cache
   * maintenance. Loopback couples both directions; these are simultaneous
   * rates. */
  CHECK(plio_buffer_device(&a));
  CHECK(plio_buffer_device(&b));
  uint64_t bench_start = plio_now_ns();
  for (unsigned i = 0; i < 64; ++i) {
    CHECK(plio_dma_start(&d, a.phys, b.phys, 8388608));
    CHECK(plio_dma_wait(&d, 5000));
  }
  double gbps = (64.0 * 8388608) / (plio_now_ns() - bench_start);
  printf("BENCH MM2S_GBps=%.6f S2MM_GBps=%.6f concurrent_GBps=%.6f target=%s\n",
         gbps, gbps, gbps * 2, gbps >= 1 ? "PASS" : "FAIL");
  CHECK(plio_buffer_cpu(&a));
  CHECK(plio_buffer_cpu(&b));
  /* Reset a completed baseline before error injection. */
  CHECK(plio_buffer_device(&a));
  CHECK(plio_buffer_device(&b));
  CHECK(
      plio_dma_start(&d, a.phys, b.phys, 4096)); /* normal recovery baseline */
  CHECK(plio_dma_wait(&d, 5000));
  CHECK(plio_dma_reset(&d, 1000));
  /* Decode error: address outside assigned DDR, no arbitrary write to memory.
   */
  CHECK(plio_dma_start(&d, 0x7000000000ULL, b.phys, 16));
  if (plio_dma_wait(&d, 1000) == 0) {
    fprintf(stderr, "Expected decode error\n");
    return 1;
  }
  puts("PASS bad address rejected; reset performed");
  CHECK(plio_dma_start(&d, a.phys, b.phys, 16));
  CHECK(plio_dma_wait(&d, 5000));
  CHECK(plio_buffer_cpu(&b));
  CHECK(plio_buffer_cpu(&a));
  if (memcmp(a.data, b.data, 16))
    return 1;
  puts("PASS recovery");
  /* Deliberately dirty cached source and destination, and omit both sync calls.
   */
  memset(a.data, 0x3d, 256);
  memset(b.data, 0xc7, 256);
  CHECK(plio_dma_start(&d, a.phys, b.phys, 256));
  CHECK(plio_dma_wait(&d, 5000));
  printf("SKIP_SYNC stale_observed=%d (diagnostic; failure is expected)\n",
         memcmp(a.data, b.data, 256) != 0);
  CHECK(plio_buffer_device(&a));
  CHECK(plio_buffer_device(&b));
  CHECK(plio_dma_start(&d, a.phys, b.phys, 256));
  CHECK(plio_dma_wait(&d, 5000));
  CHECK(plio_buffer_cpu(&b));
  CHECK(plio_buffer_cpu(&a));
  if (memcmp(a.data, b.data, 256))
    return 1;
  puts("PASS cache maintenance");
  plio_dma_close(&d);
  plio_buffer_close(&a);
  plio_buffer_close(&b);
  return 0;
}
