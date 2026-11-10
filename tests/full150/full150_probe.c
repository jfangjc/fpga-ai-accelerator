/* Board acceptance and alternating CPU/PL timing; no driver modifications. */
#include "../../sw/engdrv/engdrv.h"
#include "../../sw/qnrt/qnrt.h"
#include "../../sw/plio/plio.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void check(int rc) { if (rc) { perror("full150"); exit(1); } }
static unsigned ctc(const int8_t *x, unsigned t, unsigned v, unsigned *ids) {
 unsigned n = 0, prev = 29;
 for (unsigned j = 0; j < v; ++j) {
  unsigned best = 0;
  for (unsigned c = 1; c < 29; ++c) if (x[c*t+j] > x[best*t+j]) best = c;
  if (best != prev && best != 28) ids[n++] = best;
  prev = best;
 }
 return n;
}
static void compare(const int8_t *a, unsigned at, unsigned av,
                    const int8_t *b, unsigned bt, unsigned bv) {
 unsigned ai[ENGDRV_MAX_FRAMES], bi[ENGDRV_MAX_FRAMES];
 if (at != bt || av != bv || memcmp(a, b, 29*at)) {
  fprintf(stderr, "FAIL logits/shape\n"); exit(1);
 }
 unsigned an = ctc(a, at, av, ai), bn = ctc(b, bt, bv, bi);
 if (an != bn || memcmp(ai, bi, an*sizeof(*ai))) {
  fprintf(stderr, "FAIL CTC\n"); exit(1);
 }
}
int main(int argc, char **argv) {
 if (argc != 7) {
  fprintf(stderr, "usage: full150_probe program cpu features frames valid pairs\n");
  return 2;
 }
 unsigned t = strtoul(argv[4], NULL, 10), v = strtoul(argv[5], NULL, 10);
 unsigned pairs = strtoul(argv[6], NULL, 10);
 if (!t || t > ENGDRV_MAX_FRAMES || v > t) return 2;
 FILE *f = fopen(argv[3], "rb"); if (!f) return 1;
 int8_t *x = malloc(64*t);
 if (!x || fread(x, 64, t, f) != t || fgetc(f) != EOF) return 1;
 fclose(f);
 engdrv *e; qnrt *q;
 struct engdrv_config cfg = {0, 2, 3, 10000};
 struct engdrv_timing timing;
 check(engdrv_open(&e, &cfg));
 uint32_t version; check(engdrv_version(e, &version));
 check(engdrv_ping(e, &timing));
 check(engdrv_load(e, argv[1]));
 printf("CAPABILITY version=%08x network=%d ping_cycles=%llu\n", version,
        engdrv_network_capable(e), (unsigned long long)timing.cycles);
 if (version < 0x10002 || !engdrv_network_capable(e)) return 1;
 check(qnrt_load(&q, argv[2], ENGDRV_MAX_FRAMES, 4));
 const int8_t *a, *b; unsigned at, av, bt, bv;
 /* Two warm-up pairs followed by alternating execution order. */
 for (unsigned i = 0; i < (pairs ? pairs+2 : 1); ++i) {
  uint64_t cpu_ns = 0, pl_ns = 0;
  for (unsigned k = 0; k < 2; ++k) {
   uint64_t start = plio_now_ns();
   if ((k ^ (i & 1)) == 0) {
    check(qnrt_run(q, x, t, v, &b, &bt, &bv)); cpu_ns = plio_now_ns()-start;
   } else {
    check(engdrv_run_network(e, x, t, v, &a, &at, &av, &timing)); pl_ns = plio_now_ns()-start;
   }
  }
  compare(a, at, av, b, bt, bv);
  struct engdrv_run_info info; check(engdrv_last_run(e, &info));
  if (info.network != (t <= ENGDRV_NETWORK_FRAMES) ||
      (info.network && info.commands != 1)) return 1;
  if (!pairs || i >= 2) {
   printf("PASS pair=%u input=%u/%u logits=%u ctc=identical mode=%s commands=%u cpu_ms=%.6f pl_ms=%.6f cycles=%llu sync_ms=%.6f\n",
     pairs ? i-2 : 0, t, v, 29*at, info.network ? "network" : "layer",
     info.commands, cpu_ns/1e6, pl_ns/1e6,
     (unsigned long long)timing.cycles, info.sync_ns/1e6);
   fflush(stdout);
  }
 }
 engdrv_close(e); qnrt_free(q); free(x);
 return 0;
}
