#define _POSIX_C_SOURCE 200809L
#include "../frontend_c/frontend.h"
#include "qnrt.h"
#include "wav.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <time.h>
static double now(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return t.tv_sec + t.tv_nsec * 1e-9;
}
int main(int argc, char **argv) {
  if (argc < 3) {
    fprintf(stderr,
            "usage: qnrt_transcribe weights wav [--mode utterance|running] "
            "[--prior json] [--threads 1|2|4] [--bench] [--runs N] [--features path]\n");
    return 2;
  }
  int running = 0, reps = 1;
  unsigned threads = 4;
  char *prior = NULL, *dump = NULL;
  for (int j = 3; j < argc; j++) {
    if (!strcmp(argv[j], "--mode") && j + 1 < argc) {
      const char *mode = argv[++j];
      if (strcmp(mode, "utterance") && strcmp(mode, "running"))
        return 2;
      running = !strcmp(mode, "running");
    } else if (!strcmp(argv[j], "--prior") && j + 1 < argc)
      prior = argv[++j];
    else if (!strcmp(argv[j], "--threads") && j + 1 < argc)
      threads = atoi(argv[++j]);
    else if (!strcmp(argv[j], "--bench"))
      reps = 6;
    else if (!strcmp(argv[j], "--runs") && j + 1 < argc) {
      char *end;
      long count = strtol(argv[++j], &end, 10);
      if (*end || count < 1 || count > 10000)
        return 2;
      reps = (int)count + 1; /* warmup plus N measured samples */
    }
    else if (!strcmp(argv[j], "--features") && j + 1 < argc)
      dump = argv[++j];
    else
      return 2;
  }
  size_t n;
  float *x = qn_wav(argv[2], &n);
  if (!x) {
    fprintf(stderr,
            "expected mono PCM16 16kHz WAV; convert FLAC with prepare.py\n");
    return 1;
  }
  unsigned T = (n / 160 + 16) / 16 * 16;
  qnrt *c;
  if (qnrt_load(&c, argv[1], T, threads))
    return 1;
  float *f = malloc((size_t)64 * T * sizeof(float));
  int8_t *q = malloc((size_t)64 * T);
  if (!f || !q)
    return 1;
  qnf_state state = {0};
  if (running && prior && qnf_prior(&state, prior))
    return 1;
  qnf_state initial = state;
  double start = now();
  if (qnf_features(x, n, running, &state, f) != (int)T)
    return 1;
  qnf_quantize(f, (size_t)64 * T, qnrt_input_scale(c), q);
  double front = now() - start;
  if (dump) {
    FILE *d = fopen(dump, "wb");
    if (!d)
      return 1;
    fwrite(f, sizeof(float), (size_t)64 * T, d);
    fclose(d);
    char path[4096];
    snprintf(path, sizeof(path), "%s.int8", dump);
    d = fopen(path, "wb");
    if (!d)
      return 1;
    fwrite(q, 1, (size_t)64 * T, d);
    fclose(d);
  }
  const int8_t *y = NULL;
  unsigned t, v;
  for (int r = 0; r < reps; r++) {
    if (r) {
      state = initial;
      double fs = now();
      if (qnf_features(x, n, running, &state, f) != (int)T)
        return 1;
      qnf_quantize(f, (size_t)64 * T, qnrt_input_scale(c), q);
      front = now() - fs;
    }
    double s = now();
    if (qnrt_run(c, q, T, n / 160 + 1, &y, &t, &v))
      return 1;
    struct rusage u;
    getrusage(RUSAGE_SELF, &u);
    fprintf(
        stderr,
        "run=%d frontend=%.9f network=%.9f audio=%.6f rss_kib=%ld threads=%u\n",
        r, front, now() - s, n / 16000., u.ru_maxrss, threads);
  }
  const char *vocab = " abcdefghijklmnopqrstuvwxyz'";
  int prev = -1;
  for (unsigned j = 0; j < v; j++) {
    int best = 0;
    for (int k = 1; k < 29; k++)
      if (y[(size_t)k * t + j] > y[(size_t)best * t + j])
        best = k;
    if (best != prev && best != 28)
      putchar(vocab[best]);
    prev = best;
  }
  putchar('\n');
  qnrt_free(c);
  free(x);
  free(f);
  free(q);
  return 0;
}
