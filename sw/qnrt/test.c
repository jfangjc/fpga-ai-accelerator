#define _POSIX_C_SOURCE 200809L
#include "qnrt.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
static void *readfile(const char *p, size_t n) {
  FILE *f = fopen(p, "rb");
  if (!f)
    return NULL;
  void *b = malloc(n);
  if (!b || fread(b, 1, n, f) != n) {
    free(b);
    b = NULL;
  }
  fclose(f);
  return b;
}
static double now(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return t.tv_sec + t.tv_nsec * 1e-9;
}
int main(int argc, char **argv) {
  if (argc < 4) {
    fprintf(stderr, "usage: qnrt_test blob vectors.tsv threads [bench]\n");
    return 2;
  }
  qnrt *c;
  unsigned nt = atoi(argv[3]);
  if (qnrt_load(&c, argv[1], 2048, nt)) {
    fprintf(stderr, "load failed\n");
    return 1;
  }
  FILE *f = fopen(argv[2], "r");
  if (!f)
    return 2;
  unsigned i, ci, co, t, v, ot, ov, n = 0;
  char a[512], b[512], y[512];
  while (fscanf(f, "%u %u %u %u %u %u %u %511s %511s %511s", &i, &ci, &co, &t,
                &v, &ot, &ov, a, b, y) == 10) {
    void *x = readfile(a, (size_t)ci * t),
         *z = strcmp(b, "-") ? readfile(b, (size_t)ci * t) : NULL,
         *gold = readfile(y, (size_t)co * ot), *out = malloc((size_t)co * ot);
    unsigned got = 0, valid = 0;
    if (!x || !gold || !out ||
        qnrt_layer(c, i, x, z, t, v, out, &got, &valid) || got != ot ||
        valid != ov || memcmp(out, gold, (size_t)co * ot)) {
      fprintf(stderr, "FAIL vector %u layer %u\n", n, i);
      return 1;
    }
    if (argc > 5 && n < qnrt_layers(c)) {
      double runs[6];
      for (int r = 0; r < 6; r++) {
        double start = now();
        if (qnrt_layer(c, i, x, z, t, v, out, &got, &valid))
          return 1;
        runs[r] = now() - start;
      }
      for (int a = 1; a < 6; a++)
        for (int b = a + 1; b < 6; b++)
          if (runs[a] > runs[b]) {
            double tmp = runs[a];
            runs[a] = runs[b];
            runs[b] = tmp;
          }
      printf("profile layer=%u seconds=%.9f frames=%u\n", i, runs[3], ot);
    }
    free(x);
    free(z);
    free(gold);
    free(out);
    n++;
  }
  fclose(f);
  printf("PASS %u layer vectors threads=%u\n", n, nt);
  for (unsigned T = 1; T <= 101; T = T == 1 ? 17 : 101) {
    char p[128];
    snprintf(p, sizeof(p), "clips/network_%u.in", T);
    int8_t *x = readfile(p, (size_t)64 * T);
    if (!x) {
      fprintf(stderr, "missing network fixture %s\n", p);
      return 2;
    }
    snprintf(p, sizeof(p), "clips/network_%u.out", T);
    unsigned N = (T + 1) / 2;
    void *g = readfile(p, (size_t)29 * N);
    const int8_t *y;
    unsigned ot, ov;
    if (!g || qnrt_run(c, x, T, T, &y, &ot, &ov) || ot != N || ov != N ||
        memcmp(g, y, (size_t)29 * N)) {
      fprintf(stderr, "FAIL network T=%u\n", T);
      return 1;
    }
    printf("PASS network T=%u\n", T);
    free(x);
    free(g);
    if (T == 101)
      break;
  }
  if (argc > 4) {
    int8_t *x = calloc(64, 304);
    const int8_t *y;
    unsigned t, v;
    for (int r = 0; r < 6; r++) {
      double s = now();
      if (qnrt_run(c, x, 304, 300, &y, &t, &v))
        return 1;
      printf("bench threads=%u run=%d seconds=%.9f output=%u valid=%u\n", nt, r,
             now() - s, t, v);
    }
    free(x);
  }
  qnrt_free(c);
  return n ? 0 : 1;
}
