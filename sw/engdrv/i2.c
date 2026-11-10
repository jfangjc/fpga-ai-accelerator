#include "../qnrt/qnrt.h"
#include "engdrv.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static unsigned ctc(const int8_t *x, unsigned t, unsigned v, unsigned *ids) {
  unsigned n = 0, previous = 29;
  for (unsigned j = 0; j < v; j++) {
    unsigned best = 0;
    for (unsigned c = 1; c < 29; c++)
      if (x[c * t + j] > x[best * t + j])
        best = c;
    if (best != previous && best != 28)
      ids[n++] = best;
    previous = best;
  }
  return n;
}
int main(int argc, char **argv) {
  if (argc < 6) {
    fprintf(stderr,
            "usage: i2 PROGRAM.qnp2 CPU.qnw1 FEATURES.i8 T VALID [cosim]\n");
    return 2;
  }
  unsigned t = strtoul(argv[4], 0, 10), v = strtoul(argv[5], 0, 10);
  if (!t || t > ENGDRV_MAX_FRAMES || v > t)
    return 2;
  FILE *f = fopen(argv[3], "rb");
  if (!f)
    return 1;
  int8_t *x = malloc(64 * t);
  if (!x || fread(x, 64, t, f) != t || fgetc(f) != EOF)
    return 1;
  fclose(f);
  engdrv *e;
  qnrt *q;
  struct engdrv_config c = {argc > 6, 2, 3, 600000};
  if (engdrv_open(&e, &c) || engdrv_load(e, argv[1]) ||
      qnrt_load(&q, argv[2], ENGDRV_MAX_FRAMES, 1)) {
    perror("load");
    return 1;
  }
  const int8_t *a, *b;
  unsigned at, av, bt, bv;
  struct engdrv_timing timing;
  if (engdrv_run_network(e, x, t, v, &a, &at, &av, &timing) ||
      qnrt_run(q, x, t, v, &b, &bt, &bv)) {
    perror("run");
    return 1;
  }
  static unsigned ai[ENGDRV_MAX_FRAMES], bi[ENGDRV_MAX_FRAMES];
  unsigned an = ctc(a, at, av, ai), bn = ctc(b, bt, bv, bi);
  int rc = at != bt || av != bv || memcmp(a, b, 29 * at) || an != bn ||
           memcmp(ai, bi, an * sizeof(unsigned));
  struct engdrv_run_info info = {0};
  engdrv_last_run(e, &info);
  printf("%s I2 logits+CTC T=%u valid=%u cycles=%llu wall_ms=%.3f mode=%s "
         "commands=%u exchange_ms=%.3f outside_ms=%.3f sync_ms=%.3f "
         "sync_calls=%u\n",
         rc ? "FAIL" : "PASS", at, av, (unsigned long long)timing.cycles,
         (timing.ready_ns - timing.submit_ns) / 1e6,
         info.network ? "network" : "layer", info.commands,
         info.exchange_ns / 1e6, info.outside_ns / 1e6, info.sync_ns / 1e6,
         info.sync_calls);
  engdrv_close(e);
  qnrt_free(q);
  free(x);
  return rc;
}
