#include "plio.h"
#include <stdio.h>
#include <string.h>
int main(void) {
  for (unsigned i = 0; i < 2; i++) {
    struct plio_buffer b;
    if (plio_buffer_open(&b, i)) {
      perror("buffer");
      return 1;
    }
    uint64_t start = plio_now_ns();
    memset(b.data, 0xa5, b.size);
    if (plio_buffer_device(&b) || plio_buffer_cpu(&b)) {
      perror("sync");
      return 1;
    }
    for (size_t j = 0; j < b.size; j++)
      if (((unsigned char *)b.data)[j] != 0xa5)
        return 1;
    printf("buffer=%u phys=0x%llx bytes=%zu write_sync_read_ms=%.3f PASS\n", i,
           (unsigned long long)b.phys, b.size, (plio_now_ns() - start) / 1e6);
    plio_buffer_close(&b);
  }
  return 0;
}
