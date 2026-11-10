#define _POSIX_C_SOURCE 200809L
#include "qnrt.h"
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
static double now(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return t.tv_sec + t.tv_nsec * 1e-9;
}
/* Already-quantised representative window, with one warmup excluded. */
int main(int argc, char **argv) {
  if (argc != 7) {
    fprintf(stderr, "usage: qnrt_bench blob features frames valid threads runs\n");
    return 2;
  }
  unsigned T = atoi(argv[3]), valid = atoi(argv[4]), nt = atoi(argv[5]);
  unsigned reps = atoi(argv[6]);
  if (!T || valid > T || !reps || reps > 10000)
    return 2;
  qnrt *c;
  if (qnrt_load(&c, argv[1], T, nt))
    return 1;
  int8_t *x = malloc((size_t)64 * T);
  FILE *f = fopen(argv[2], "rb");
  if (!x || !f || fread(x, 64, T, f) != T)
    return 1;
  fclose(f);
  double *profile = calloc(qnrt_layers(c), sizeof(double));
  if (!profile)
    return 1;
  for (unsigned r = 0; r <= reps; r++) {
    const int8_t *y;
    unsigned ot, ov;
    double start = now();
    if (qnrt_run(c, x, T, valid, &y, &ot, &ov))
      return 1;
    printf("run=%u seconds=%.9f macs=%llu output=%u valid=%u\n", r,
           now() - start, (unsigned long long)qnrt_macs(c, T), ot, ov);
    fflush(stdout);
  }
  /* Separate profiling pass avoids clock reads in accepted network timings. */
  qnrt_profile(c, profile);
  const int8_t *y;
  unsigned ot, ov;
  if (qnrt_run(c, x, T, valid, &y, &ot, &ov))
    return 1;
  for (unsigned i = 0; i < qnrt_layers(c); i++)
    printf("layer=%u kind=%d seconds=%.9f\n", i,
           qnrt_layer_kind(c, i), profile[i]);
  free(profile);
  free(x);
  qnrt_free(c);
  return 0;
}
