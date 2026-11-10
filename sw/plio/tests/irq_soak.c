#include "plio.h"
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char **argv) {
  setvbuf(stdout, NULL, _IOLBF, 0);
  unsigned seconds = argc > 1 ? strtoul(argv[1], NULL, 0) : 1800;
  struct plio_uio u;
  if (plio_uio_open(&u, "stt-timer")) {
    perror("timer");
    return 1;
  }
  u.regs[0] = 0;
  u.regs[1] = 999998;
  u.regs[0] = 0x20;
  u.regs[0] = 0x1d2;
  if (plio_irq_enable(&u))
    return 1;
  uint64_t start = plio_now_ns(), end = start + (uint64_t)seconds * 1000000000,
           count = 0;
  unsigned gaps = 0;
  unsigned late = 0;
  unsigned missing_periods = 0;
  int64_t previous_phase = 0;
  uint64_t previous = start, max_interval = 0;
  while (plio_now_ns() < end) {
    uint32_t delta;
    if (plio_irq_wait(&u, 1000, &delta)) {
      perror("irq");
      return 1;
    }
    count++;
    uint64_t now = plio_now_ns(), interval = now - previous;
    /* TCR0 is a live down-counter. Remove the within-period IRQ latency from
     * successive timestamps to distinguish coalesced periods from clock drift.
     */
    int64_t phase = (999998 - (int64_t)u.regs[2]) * 10;
    if (count > 1) {
      int64_t periods =
          ((int64_t)interval + previous_phase - phase + 5000000) / 10000000;
      if (periods != 1)
        missing_periods += periods > 1 ? periods - 1 : 1;
    }
    previous_phase = phase;
    previous = now;
    if (interval > max_interval)
      max_interval = interval;
    /* Each 20 ms coalesced period exceeds this bound. Clock drift between PS
     * monotonic and the PL oscillator cannot accumulate in this local check. */
    if (count > 1 && interval > 15000000)
      late++;
    if (count % 6000 == 0)
      printf("progress count=%llu elapsed_s=%.3f\n", (unsigned long long)count,
             (plio_now_ns() - start) * 1e-9);
    if (count > 1 && delta != 1)
      gaps++;
    u.regs[0] = 0x1d2;
    if (plio_irq_enable(&u))
      return 1;
  }
  u.regs[0] = 0x100;
  double elapsed = (plio_now_ns() - start) * 1e-9;
  double expected = elapsed * 100;
  printf("elapsed=%.6f count=%llu expected=%.2f uio_gaps=%u deficit=%.2f\n",
         elapsed, (unsigned long long)count, expected, gaps, expected - count);
  printf("max_interval_ms=%.6f intervals_over_15ms=%u\n", max_interval / 1e6,
         late);
  printf("missing_hardware_periods=%u\n", missing_periods);
  plio_uio_close(&u);
  return gaps || late || missing_periods ? 1 : 0;
}
