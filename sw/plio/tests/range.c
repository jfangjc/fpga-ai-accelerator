#include "plio.h"
#include <stdio.h>
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
  uint64_t start = plio_now_ns();
  for (unsigned i = 0; i < 100; i++) {
    memset((char *)a.data + 64, i, 256);
    memset((char *)b.data + 64, 255 - i, 256);
    CHECK(plio_buffer_sync_range(&a, 64, 256, 0));
    CHECK(plio_buffer_sync_range(&b, 64, 256, 0));
    CHECK(plio_dma_start_sized(&d, a.phys + 64, 255, b.phys + 64, 256));
    CHECK(plio_dma_wait(&d, 1000));
    if (plio_dma_received_bytes(&d) != 255)
      return 1;
    CHECK(plio_buffer_sync_range(&a, 64, 256, 1));
    CHECK(plio_buffer_sync_range(&b, 64, 256, 1));
    if (memcmp((char *)a.data + 64, (char *)b.data + 64, 255) ||
        ((unsigned char *)b.data)[319] != 255 - i)
      return 1;
  }
  printf("PASS range-sync 100 transfers of 255 bytes; mean_roundtrip_us=%.3f\n",
         (plio_now_ns() - start) / 100000.0);
  plio_dma_close(&d);
  plio_buffer_close(&a);
  plio_buffer_close(&b);
  return 0;
}
