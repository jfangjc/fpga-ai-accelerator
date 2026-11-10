/* AArch64 NEON integer multiply-accumulate ceiling microbenchmark.
 *
 * Each variant runs register-only independent accumulator chains (no memory
 * traffic) so the result is an issue-throughput ceiling, not a GEMM rate.
 * "freq" times a chain of 1-cycle dependent integer adds to estimate the core
 * clock without PMU access.  Usage: ./peak [iterations]
 */
#define _POSIX_C_SOURCE 200809L
#include <arm_neon.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#define BAR(r) __asm__ volatile("" : "+w"(r))
static volatile uint32_t sink;
static unsigned iters = 20000000;
typedef struct {
  const char *name;
  double macs; /* per loop iteration */
  uint32_t (*fn)(unsigned);
} variant;
/* 8 x SMLAL Vd.4S, Vn.4H, Vm.4H: 32 MAC */
static uint32_t v_smlal16(unsigned n) {
  int16x4_t x = vdup_n_s16(3), w = vdup_n_s16(7);
  int32x4_t a[8];
  _Pragma("GCC unroll 8") for (int j = 0; j < 8; j++)
    a[j] = vdupq_n_s32(j);
  for (unsigned i = 0; i < n; i++)
    for (int j = 0; j < 8; j++) {
      a[j] = vmlal_s16(a[j], x, w);
      BAR(a[j]);
    }
  return (uint32_t)vgetq_lane_s32(a[0], 0) + vgetq_lane_s32(a[7], 1);
}
/* 8 x SMLAL Vd.4S, Vn.4H, Vm.H[lane]: 32 MAC */
static uint32_t v_smlal16_lane(unsigned n) {
  int16x4_t x = vdup_n_s16(3), w = vdup_n_s16(7);
  int32x4_t a[8];
  _Pragma("GCC unroll 8") for (int j = 0; j < 8; j++)
    a[j] = vdupq_n_s32(j);
  for (unsigned i = 0; i < n; i++) {
    a[0] = vmlal_lane_s16(a[0], x, w, 0);
    a[1] = vmlal_lane_s16(a[1], x, w, 1);
    a[2] = vmlal_lane_s16(a[2], x, w, 2);
    a[3] = vmlal_lane_s16(a[3], x, w, 3);
    a[4] = vmlal_lane_s16(a[4], x, w, 0);
    a[5] = vmlal_lane_s16(a[5], x, w, 1);
    a[6] = vmlal_lane_s16(a[6], x, w, 2);
    a[7] = vmlal_lane_s16(a[7], x, w, 3);
    _Pragma("GCC unroll 8") for (int j = 0; j < 8; j++)
      BAR(a[j]);
  }
  return (uint32_t)vgetq_lane_s32(a[0], 0) + vgetq_lane_s32(a[7], 1);
}
/* 8 x MLA Vd.8H (non-widening int16): 64 MAC */
static uint32_t v_mla16q(unsigned n) {
  int16x8_t x = vdupq_n_s16(3), w = vdupq_n_s16(7);
  int16x8_t a[8];
  _Pragma("GCC unroll 8") for (int j = 0; j < 8; j++)
    a[j] = vdupq_n_s16(j);
  for (unsigned i = 0; i < n; i++)
    for (int j = 0; j < 8; j++) {
      a[j] = vmlaq_s16(a[j], x, w);
      BAR(a[j]);
    }
  return (uint32_t)vgetq_lane_s16(a[0], 0) + vgetq_lane_s16(a[7], 1);
}
/* 8 x SMLAL Vd.8H, Vn.8B, Vm.8B: 64 MAC */
static uint32_t v_smlal8(unsigned n) {
  int8x8_t x = vdup_n_s8(3), w = vdup_n_s8(7);
  int16x8_t a[8];
  _Pragma("GCC unroll 8") for (int j = 0; j < 8; j++)
    a[j] = vdupq_n_s16(j);
  for (unsigned i = 0; i < n; i++)
    for (int j = 0; j < 8; j++) {
      a[j] = vmlal_s8(a[j], x, w);
      BAR(a[j]);
    }
  return (uint32_t)vgetq_lane_s16(a[0], 0) + vgetq_lane_s16(a[7], 1);
}
/* 8 x {SMULL 8B, SMLAL2 16B, SADALP 4S}: 128 MAC.  The int8 GEMM inner step;
 * inline assembly so the compiler cannot share the identical products. */
#define P(t, a)                                                                \
  "smull " t ".8h, %[x].8b, %[w].8b; smlal2 " t ".8h, %[x].16b, %[w].16b;"     \
  "sadalp %" a ".4s, " t ".8h;"
#define ACC8                                                                   \
  "+w"(a0), "+w"(a1), "+w"(a2), "+w"(a3), "+w"(a4), "+w"(a5), "+w"(a6),        \
      "+w"(a7)
#define TMP8 "v24", "v25", "v26", "v27", "v28", "v29", "v30", "v31"
static uint32_t v_int8_pair(unsigned n) {
  int8x16_t x = vdupq_n_s8(3), w = vdupq_n_s8(7);
  int32x4_t a0 = vdupq_n_s32(0), a1 = a0, a2 = a0, a3 = a0, a4 = a0, a5 = a0,
            a6 = a0, a7 = a0;
  for (unsigned i = 0; i < n; i++)
    __asm__ volatile(P("v24", "0") P("v25", "1") P("v26", "2") P("v27", "3")
                         P("v28", "4") P("v29", "5") P("v30", "6")
                             P("v31", "7")
                     : ACC8
                     : [x] "w"(x), [w] "w"(w)
                     : TMP8);
  return (uint32_t)vgetq_lane_s32(a0, 0) + vgetq_lane_s32(a7, 1);
}
/* Same mix, interleaved so no instruction waits on its predecessor. */
static uint32_t v_int8_sched(unsigned n) {
  int8x16_t x = vdupq_n_s8(3), w = vdupq_n_s8(7);
  int32x4_t a0 = vdupq_n_s32(0), a1 = a0, a2 = a0, a3 = a0, a4 = a0, a5 = a0,
            a6 = a0, a7 = a0;
  for (unsigned i = 0; i < n; i++)
    __asm__ volatile(
        "smull v24.8h, %[x].8b, %[w].8b; smull v25.8h, %[x].8b, %[w].8b;"
        "smull v26.8h, %[x].8b, %[w].8b; smull v27.8h, %[x].8b, %[w].8b;"
        "smlal2 v24.8h, %[x].16b, %[w].16b; smlal2 v25.8h, %[x].16b, %[w].16b;"
        "smlal2 v26.8h, %[x].16b, %[w].16b; smlal2 v27.8h, %[x].16b, %[w].16b;"
        "smull v28.8h, %[x].8b, %[w].8b; smull v29.8h, %[x].8b, %[w].8b;"
        "smull v30.8h, %[x].8b, %[w].8b; smull v31.8h, %[x].8b, %[w].8b;"
        "sadalp %0.4s, v24.8h; sadalp %1.4s, v25.8h;"
        "sadalp %2.4s, v26.8h; sadalp %3.4s, v27.8h;"
        "smlal2 v28.8h, %[x].16b, %[w].16b; smlal2 v29.8h, %[x].16b, %[w].16b;"
        "smlal2 v30.8h, %[x].16b, %[w].16b; smlal2 v31.8h, %[x].16b, %[w].16b;"
        "sadalp %4.4s, v28.8h; sadalp %5.4s, v29.8h;"
        "sadalp %6.4s, v30.8h; sadalp %7.4s, v31.8h;"
        : ACC8
        : [x] "w"(x), [w] "w"(w)
        : TMP8);
  return (uint32_t)vgetq_lane_s32(a0, 0) + vgetq_lane_s32(a7, 1);
}
/* 8 x SADALP 4S alone: pairwise-accumulate issue rate (reported as Ginstr/s). */
static uint32_t v_sadalp(unsigned n) {
  int16x8_t x = vdupq_n_s16(3);
  int32x4_t a0 = vdupq_n_s32(0), a1 = a0, a2 = a0, a3 = a0, a4 = a0, a5 = a0,
            a6 = a0, a7 = a0;
  for (unsigned i = 0; i < n; i++)
    __asm__ volatile("sadalp %0.4s, %[x].8h; sadalp %1.4s, %[x].8h;"
                     "sadalp %2.4s, %[x].8h; sadalp %3.4s, %[x].8h;"
                     "sadalp %4.4s, %[x].8h; sadalp %5.4s, %[x].8h;"
                     "sadalp %6.4s, %[x].8h; sadalp %7.4s, %[x].8h;"
                     : ACC8
                     : [x] "w"(x));
  return (uint32_t)vgetq_lane_s32(a0, 0) + vgetq_lane_s32(a7, 1);
}
static uint32_t v_freq(unsigned n) {
  uint64_t x = 0;
  for (unsigned i = 0; i < n; i++)
    __asm__ volatile(".rept 32\nadd %0, %0, #1\n.endr" : "+r"(x));
  return (int)x;
}
static const variant variants[] = {
    {"smlal_4h_vec", 32, v_smlal16},
    {"smlal_4h_lane", 32, v_smlal16_lane},
    {"mla_8h_int16", 64, v_mla16q},
    {"smlal_8b_int16acc", 64, v_smlal8},
    {"int8_smull_smlal2_sadalp", 128, v_int8_pair},
    {"int8_sched", 128, v_int8_sched},
    {"sadalp_only_ginstr", 8, v_sadalp},
    {"freq_add_chain", 32, v_freq},
};
typedef struct {
  const variant *v;
  uint32_t result;
} job;
static void *work(void *p) {
  job *j = p;
  j->result = j->v->fn(iters);
  return NULL;
}
static double now(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return t.tv_sec + t.tv_nsec * 1e-9;
}
static int cmp(const void *a, const void *b) {
  double x = *(const double *)a, y = *(const double *)b;
  return x < y ? -1 : x > y;
}
int main(int argc, char **argv) {
  if (argc > 1)
    iters = (unsigned)strtoul(argv[1], NULL, 10);
  double hz = 0;
  for (size_t v = 0; v < sizeof(variants) / sizeof(*variants); v++) {
    const variant *var = variants + v;
    int freq = !strcmp(var->name, "freq_add_chain");
    for (unsigned nt = 1; nt <= 4; nt *= 2) {
      if (freq && nt > 1)
        break;
      double runs[6];
      for (int r = 0; r < 6; r++) {
        pthread_t ts[4];
        job jobs[4];
        double s = now();
        for (unsigned j = 0; j < nt; j++) {
          jobs[j].v = var;
          pthread_create(ts + j, NULL, work, jobs + j);
        }
        for (unsigned j = 0; j < nt; j++) {
          pthread_join(ts[j], NULL);
          sink += jobs[j].result;
        }
        runs[r] = now() - s;
      }
      qsort(runs + 1, 5, sizeof(double), cmp); /* run 0 is warmup */
      double sec = runs[3], rate = nt * var->macs * iters / sec;
      if (freq) {
        hz = rate;
        printf("freq_add_chain threads=1 seconds=%.6f est_hz=%.4e\n", sec, hz);
      } else
        printf("variant=%s threads=%u seconds=%.6f gmac_s=%.4f\n", var->name,
               nt, sec, rate / 1e9);
    }
  }
  return sink == 42;
}
