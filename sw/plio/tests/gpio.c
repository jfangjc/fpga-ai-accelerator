#define _POSIX_C_SOURCE 200809L
#include "plio.h"
#include <stdatomic.h>
#include <stdio.h>
#include <time.h>
int main(void) {
  struct plio_uio u;
  if (plio_uio_open(&u, "stt-gpio")) {
    perror("GPIO");
    return 1;
  }
  for (unsigned i = 0; i < 4; i++) {
    u.regs[0] = i & 1;
    atomic_thread_fence(memory_order_seq_cst);
    printf("LED_register=%u button=%u\n", u.regs[0] & 1, u.regs[2] & 1);
    struct timespec t = {0, 250000000};
    nanosleep(&t, NULL);
  }
  u.regs[0] = 0;
  plio_uio_close(&u);
  return 0;
}
