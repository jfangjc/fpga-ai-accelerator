#define _POSIX_C_SOURCE 200809L
#include "qnrt.h"
#include <limits.h>
#include <math.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#if defined(__aarch64__) && !defined(QNRT_SCALAR)
#define QNRT_NEON 1
#include <arm_neon.h>
#endif
/* Activations are time-major internally: row t holds all channels of frame t,
 * row stride S = channels rounded up to 64 bytes. Every buffer has ROWPAD
 * spare rows so 4-row kernel blocks and one-step-ahead loads stay in bounds. */
#define ROWPAD 8
#define PW_OCHUNK 64 /* pointwise work unit: 64 output channels ... */
#define PW_TCHUNK 16 /* ... by 16 frames */
#define DW_TCHUNK 64 /* depthwise work unit: 64 channels by 64 frames */
#define ROW_TCHUNK 16
#define SPIN 200000
enum { PW, DW, GEN, ADD };
typedef struct {
  uint32_t d[32];
  unsigned cin, uns, kind;
  int8_t *w;     /* logical [O][K] (scalar build and general convolution) */
  int32_t *p[4]; /* descriptor arrays as stored */
  int8_t *wp;    /* pointwise [O/4][K/16][4 rows][16 k] */
  int16_t *wd;   /* depthwise [C/64 slice][tap][64 channels] */
  int32_t *bias; /* accumulator start, offset-corrected for unsigned input */
  int32_t *m[2]; /* per-channel multiplier, 0 when the shift underflows */
  int64_t *n[2]; /* per-channel negated rounding shift */
  uint8_t *padrow; /* pointwise output for masked (all-zero) input frames */
} layer;
struct qnrt {
  unsigned n, max, nt, created, npool;
  float scale;
  layer *l;
  unsigned *ts, *vs, *slot;
  uint8_t **pool, *scr[3];
  int8_t *logits;
  int16_t *stage[4];
  double *prof;
  pthread_t th[3];
  pthread_mutex_t mu;
  pthread_cond_t cv;
  atomic_uint gen, pending, sleepers;
  atomic_int stop, error;
  /* current job */
  const layer *jl;
  const uint8_t *ja, *jb;
  uint8_t *jy;
  unsigned jt, jv, jot, sa, sb, sy, units;
};
static unsigned up(unsigned x, unsigned m) { return (x + m - 1) / m * m; }
static unsigned umin(unsigned a, unsigned b) { return a < b ? a : b; }
static unsigned stride_of(unsigned ch) { return up(ch, 64); }
static uint32_t rd(const uint8_t *p) {
  return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 |
         (uint32_t)p[3] << 24;
}
/* Reference requantisation: one rounding of the exact 64-bit product. */
static int32_t rq(int32_t a, int32_t m, int32_t s, int lo, int hi) {
  int64_t n = 31LL + s, p = (int64_t)a * m;
  if (n >= 64)
    return 0;
  uint64_t z = p < 0 ? (uint64_t)-p : (uint64_t)p;
  int64_t q = (int64_t)((z + (UINT64_C(1) << (n - 1))) >> n);
  if (p < 0)
    q = -q;
  return q < lo ? lo : q > hi ? hi : (int32_t)q;
}
static int length(unsigned t, const layer *l) {
  int64_t n = (int64_t)t + 2LL * l->d[9] - (int64_t)l->d[8] * (l->d[6] - 1) - 1;
  return n < 0 ? (int)(1 - ((-n + l->d[7] - 1) / l->d[7]))
               : (int)(n / l->d[7] + 1);
}
static int val(const uint8_t *p, int u) {
  return u ? *p : (int)*(const int8_t *)p;
}
/* Scalar convolution over output frames [16u, 16u+16): any shape. */
static void conv_rows(qnrt *c, unsigned rank, unsigned u) {
  (void)rank;
  const layer *l = c->jl;
  const uint32_t *d = l->d;
  unsigned O = d[4], taps = d[6], K = d[5] * taps;
  unsigned t1 = umin(c->jot, (u + 1) * ROW_TCHUNK);
  int lo = d[3] ? 0 : -128, hi = d[3] ? 255 : 127;
  for (unsigned t = u * ROW_TCHUNK; t < t1; t++)
    for (unsigned o = 0; o < O; o++) {
      int64_t acc = l->p[0][o];
      for (unsigned k = 0; k < K; k++) {
        unsigned ci = d[10] == 1 ? k / taps : o;
        int64_t pos = (int64_t)t * d[7] + (int64_t)(k % taps) * d[8] - d[9];
        if (pos >= 0 && pos < c->jv)
          acc += val(c->ja + (size_t)pos * c->sa + ci, l->uns) *
                 l->w[(size_t)o * K + k];
      }
      if (acc < INT32_MIN || acc > INT32_MAX) {
        atomic_store(&c->error, 1);
        return;
      }
      c->jy[(size_t)t * c->sy + o] =
          (uint8_t)rq((int32_t)acc, l->p[1][o], l->p[2][o], lo, hi);
    }
}
#ifndef QNRT_NEON
static void add_rows(qnrt *c, unsigned rank, unsigned u) {
  (void)rank;
  const layer *l = c->jl;
  unsigned t1 = umin(c->jt, (u + 1) * ROW_TCHUNK);
  for (unsigned t = u * ROW_TCHUNK; t < t1; t++)
    for (unsigned o = 0; o < l->d[4]; o++) {
      int a = rq((int8_t)c->ja[(size_t)t * c->sa + o], l->p[0][o], l->p[1][o],
                 -32768, 32767);
      int b = rq((int8_t)c->jb[(size_t)t * c->sb + o], l->p[2][o], l->p[3][o],
                 -32768, 32767);
      int z = a + b;
      c->jy[(size_t)t * c->sy + o] = z < 0 ? 0 : z > 255 ? 255 : z;
    }
}
#else
/* Bit-exact vector requantisation of four lanes to saturated int32.
 * p = acc*m exactly (|p| < 2^62); adding p>>63 (-1 for negative p) turns the
 * round-half-up SRSHL into the contract's round-half-away-from-zero:
 * for p<0, -((|p|+2^(n-1))>>n) == floor((p-1+2^(n-1))/2^n). SRSHL forms
 * its rounding sum at full precision. Channels with n>=64 carry m=0. */
static inline int32x4_t rq4(int32x4_t a, int32x4_t m, int64x2_t nlo,
                            int64x2_t nhi) {
  int64x2_t lo = vmull_s32(vget_low_s32(a), vget_low_s32(m));
  int64x2_t hi = vmull_high_s32(a, m);
  lo = vrshlq_s64(vsraq_n_s64(lo, lo, 63), nlo);
  hi = vrshlq_s64(vsraq_n_s64(hi, hi, 63), nhi);
  return vcombine_s32(vqmovn_s64(lo), vqmovn_s64(hi));
}
static inline int16x4_t rq4h(int32x4_t a, const int32_t *m, const int64_t *n) {
  return vqmovn_s32(rq4(a, vld1q_s32(m), vld1q_s64(n), vld1q_s64(n + 2)));
}
/* Saturating narrow chain equals the single clamp to [0,255] or [-128,127]. */
static inline uint8x8_t narrow8(int16x8_t h, int relu) {
  return relu ? vqmovun_s16(h) : vreinterpret_u8_s8(vqmovn_s16(h));
}
/* 4 output channels x 4 frames, 16 k per step. Each int16 product lane holds
 * w[k]*x[k] + w[k+8]*x[k+8], |.| <= 2*127*128 = 32512, then SADALP folds
 * pairs into int32 lanes. Weights are 4 rows of 16 bytes per step; frames
 * are 4 rows of the time-major activation buffer. Loads for the next step
 * are issued as soon as each register is free (the last step's loads read
 * 64 weight bytes and 16 activation bytes beyond the operands; both arrays
 * are padded for that). Unsigned input is offset to x-128 by EOR 0x80; the
 * caller's bias holds +128*sum(w). */
#define SM(t, w, x) "smull " t ".8h, " w ".8b, " x ".8b\n"
#define SM2(t, w, x) "smlal2 " t ".8h, " w ".16b, " x ".16b\n"
#define SA(a, t) "sadalp %[" a "].4s, " t ".8h\n"
#define MUL4(w, a, b, c, d)                                                    \
  SM(a, w, "v20") SM(b, w, "v21") SM(c, w, "v22") SM(d, w, "v23")
#define MLA4(w, a, b, c, d)                                                    \
  SM2(a, w, "v20") SM2(b, w, "v21") SM2(c, w, "v22") SM2(d, w, "v23")
#define ACC4(i, j, k, l, a, b, c, d) SA(i, a) SA(j, b) SA(k, c) SA(l, d)
/* Output-only accumulators keep GCC below its 30-operand limit. */
#define PW_INIT \
  "movi %[a0].4s, #0\n" \
  "movi %[a1].4s, #0\n" \
  "movi %[a2].4s, #0\n" \
  "movi %[a3].4s, #0\n" \
  "movi %[a4].4s, #0\n" \
  "movi %[a5].4s, #0\n" \
  "movi %[a6].4s, #0\n" \
  "movi %[a7].4s, #0\n" \
  "movi %[a8].4s, #0\n" \
  "movi %[a9].4s, #0\n" \
  "movi %[a10].4s, #0\n" \
  "movi %[a11].4s, #0\n" \
  "movi %[a12].4s, #0\n" \
  "movi %[a13].4s, #0\n" \
  "movi %[a14].4s, #0\n" \
  "movi %[a15].4s, #0\n"
#define PW_PRE                                                                 \
  "ld1 {v16.16b, v17.16b, v18.16b, v19.16b}, [%[w]], #64\n"                    \
  "ldr q20, [%[x0]], #16\n"                                                    \
  "ldr q21, [%[x1]], #16\n"                                                    \
  "ldr q22, [%[x2]], #16\n"                                                    \
  "ldr q23, [%[x3]], #16\n"                                                    \
  "1:\n"
#define PW_EOR                                                                 \
  "movi v24.16b, #0x80\n"                                                      \
  "eor v20.16b, v20.16b, v24.16b\n"                                            \
  "eor v21.16b, v21.16b, v24.16b\n"                                            \
  "eor v22.16b, v22.16b, v24.16b\n"                                            \
  "eor v23.16b, v23.16b, v24.16b\n"
#define PW_BODY                                                                \
  MUL4("v16", "v24", "v25", "v26", "v27")                                      \
  MLA4("v16", "v24", "v25", "v26", "v27")                                      \
  "ldr q16, [%[w]], #16\n"                                                     \
  MUL4("v17", "v28", "v29", "v30", "v31")                                      \
  ACC4("a0", "a1", "a2", "a3", "v24", "v25", "v26", "v27")                     \
  MLA4("v17", "v28", "v29", "v30", "v31")                                      \
  "ldr q17, [%[w]], #16\n"                                                     \
  MUL4("v18", "v24", "v25", "v26", "v27")                                      \
  ACC4("a4", "a5", "a6", "a7", "v28", "v29", "v30", "v31")                     \
  MLA4("v18", "v24", "v25", "v26", "v27")                                      \
  "ldr q18, [%[w]], #16\n"                                                     \
  MUL4("v19", "v28", "v29", "v30", "v31")                                      \
  ACC4("a8", "a9", "a10", "a11", "v24", "v25", "v26", "v27")                   \
  MLA4("v19", "v28", "v29", "v30", "v31")                                      \
  "ldr q19, [%[w]], #16\n"                                                     \
  "ldr q20, [%[x0]], #16\n" SA("a12", "v28") "ldr q21, [%[x1]], #16\n" SA(     \
      "a13", "v29") "ldr q22, [%[x2]], #16\n" SA("a14", "v30")                 \
      "ldr q23, [%[x3]], #16\n" SA("a15", "v31") "subs %w[k], %w[k], #1\n"     \
                                                 "b.ne 1b\n"
#define PW_OPS                                                                 \
  : [a0] "=&w"(a0), [a1] "=&w"(a1), [a2] "=&w"(a2), [a3] "=&w"(a3),                \
    [a4] "=&w"(a4), [a5] "=&w"(a5), [a6] "=&w"(a6), [a7] "=&w"(a7),                \
    [a8] "=&w"(a8), [a9] "=&w"(a9), [a10] "=&w"(a10), [a11] "=&w"(a11),            \
    [a12] "=&w"(a12), [a13] "=&w"(a13), [a14] "=&w"(a14), [a15] "=&w"(a15),        \
    [w] "+r"(w), [x0] "+r"(x0), [x1] "+r"(x1), [x2] "+r"(x2), [x3] "+r"(x3),   \
    [k] "+r"(k)                                                                \
  :                                                                            \
  : "v16", "v17", "v18", "v19", "v20", "v21", "v22", "v23", "v24", "v25",      \
    "v26", "v27", "v28", "v29", "v30", "v31", "cc", "memory"
static inline void pw_block(const int8_t *w, const uint8_t *x, size_t S,
                            unsigned k, int uns, int32x4_t r[4]) {
  int32x4_t a0 = vdupq_n_s32(0), a1 = a0, a2 = a0, a3 = a0, a4 = a0, a5 = a0,
            a6 = a0, a7 = a0, a8 = a0, a9 = a0, a10 = a0, a11 = a0, a12 = a0,
            a13 = a0, a14 = a0, a15 = a0;
  const uint8_t *x0 = x, *x1 = x + S, *x2 = x + 2 * S, *x3 = x + 3 * S;
  if (uns)
    __asm__(PW_INIT PW_PRE PW_EOR PW_BODY PW_OPS);
  else
    __asm__(PW_INIT PW_PRE PW_BODY PW_OPS);
  /* r[t] lanes = output channels 0..3 of frame t */
  r[0] = vpaddq_s32(vpaddq_s32(a0, a4), vpaddq_s32(a8, a12));
  r[1] = vpaddq_s32(vpaddq_s32(a1, a5), vpaddq_s32(a9, a13));
  r[2] = vpaddq_s32(vpaddq_s32(a2, a6), vpaddq_s32(a10, a14));
  r[3] = vpaddq_s32(vpaddq_s32(a3, a7), vpaddq_s32(a11, a15));
}
static void pw_unit(qnrt *c, unsigned rank, unsigned u) {
  (void)rank;
  const layer *l = c->jl;
  unsigned O4 = up(l->d[4], 4), T = c->jt, v = c->jv;
  unsigned nT = (T + PW_TCHUNK - 1) / PW_TCHUNK, steps = (l->cin + 15) / 16;
  unsigned o0 = u / nT * PW_OCHUNK, o1 = umin(o0 + PW_OCHUNK, O4);
  unsigned t0 = u % nT * PW_TCHUNK, t1 = umin(t0 + PW_TCHUNK, T);
  unsigned tc = umin(t1, up(v, 4));
  int relu = l->d[3];
  for (unsigned o = o0; o < o1; o += 4) {
    const int8_t *w = l->wp + (size_t)o / 4 * steps * 64;
    int32x4_t bias = vld1q_s32(l->bias + o), m = vld1q_s32(l->m[0] + o);
    int64x2_t nlo = vld1q_s64(l->n[0] + o), nhi = vld1q_s64(l->n[0] + o + 2);
    for (unsigned t = t0; t < tc; t += 4) {
      int32x4_t r[4];
      pw_block(w, c->ja + (size_t)t * c->sa, c->sa, steps, l->uns, r);
      for (int j = 0; j < 4; j++) {
        int16x4_t h = vqmovn_s32(rq4(vaddq_s32(r[j], bias), m, nlo, nhi));
        uint8x8_t b = narrow8(vcombine_s16(h, h), relu);
        vst1_lane_u32((uint32_t *)(c->jy + (size_t)(t + j) * c->sy + o),
                      vreinterpret_u32_u8(b), 0);
      }
    }
  }
  for (unsigned t = t0 > v ? t0 : v; t < t1; t++)
    memcpy(c->jy + (size_t)t * c->sy + o0, l->padrow + o0, o1 - o0);
}
/* Depthwise: 64-channel slice by up to 64 output frames. Input rows the
 * kernel can touch are widened to int16 into a per-thread zero-padded stage
 * (rows outside [0, valid) are zero), so taps need no bounds tests. Vectors
 * run along channels; four output frames share each weight load. */
static inline void dw_store(uint8_t *y, const layer *l, unsigned ch,
                            const int32x4_t a[4]) {
  int16x4_t h0 = rq4h(a[0], l->m[0] + ch, l->n[0] + ch);
  int16x4_t h1 = rq4h(a[1], l->m[0] + ch + 4, l->n[0] + ch + 4);
  int16x4_t h2 = rq4h(a[2], l->m[0] + ch + 8, l->n[0] + ch + 8);
  int16x4_t h3 = rq4h(a[3], l->m[0] + ch + 12, l->n[0] + ch + 12);
  vst1q_u8(y, vcombine_u8(narrow8(vcombine_s16(h0, h1), l->d[3]),
                          narrow8(vcombine_s16(h2, h3), l->d[3])));
}
static void dw_unit(qnrt *c, unsigned rank, unsigned u) {
  const layer *l = c->jl;
  const uint32_t *d = l->d;
  unsigned C16 = up(d[4], 16), OT = c->jot;
  unsigned nT = (OT + DW_TCHUNK - 1) / DW_TCHUNK, sl = u / nT;
  unsigned c0 = sl * 64, W = umin(64, C16 - c0);
  unsigned t0 = u % nT * DW_TCHUNK, t1 = umin(t0 + DW_TCHUNK, OT);
  unsigned s = d[7], dil = d[8], taps = d[6];
  int64_t r0 = (int64_t)t0 * s - d[9];
  unsigned rows = (t1 - 1 - t0) * s + (taps - 1) * dil + 1;
  int16_t *st = c->stage[rank];
  for (unsigned i = 0; i < rows; i++) {
    int64_t r = r0 + i;
    int16_t *row = st + (size_t)i * 64;
    if (r < 0 || r >= c->jv) {
      memset(row, 0, W * sizeof(int16_t));
      continue;
    }
    const uint8_t *src = c->ja + (size_t)r * c->sa + c0;
    for (unsigned g = 0; g < W; g += 16) {
      uint8x16_t b = vld1q_u8(src + g);
      int16x8_t lo, hi;
      if (l->uns) {
        lo = vreinterpretq_s16_u16(vmovl_u8(vget_low_u8(b)));
        hi = vreinterpretq_s16_u16(vmovl_high_u8(b));
      } else {
        lo = vmovl_s8(vget_low_s8(vreinterpretq_s8_u8(b)));
        hi = vmovl_high_s8(vreinterpretq_s8_u8(b));
      }
      vst1q_s16(row + g, lo);
      vst1q_s16(row + g + 8, hi);
    }
  }
  const int16_t *wd = l->wd + (size_t)sl * taps * 64;
  size_t step = (size_t)s * 64, tapstep = (size_t)dil * 64;
  for (unsigned g = 0; g < W; g += 16) {
    unsigned ch = c0 + g;
    int32x4_t b0 = vld1q_s32(l->bias + ch), b1 = vld1q_s32(l->bias + ch + 4),
              b2 = vld1q_s32(l->bias + ch + 8),
              b3 = vld1q_s32(l->bias + ch + 12);
    unsigned t = t0;
    for (; t + 4 <= t1; t += 4) {
      const int16_t *x = st + (size_t)(t - t0) * step + g;
      int32x4_t a[4][4];
      for (int q = 0; q < 4; q++) {
        a[q][0] = b0;
        a[q][1] = b1;
        a[q][2] = b2;
        a[q][3] = b3;
      }
      const int16_t *w = wd + g;
      for (unsigned j = 0; j < taps; j++, w += 64, x += tapstep) {
        int16x8_t w0 = vld1q_s16(w), w1 = vld1q_s16(w + 8);
#pragma GCC unroll 4
        for (int q = 0; q < 4; q++) {
          int16x8_t x0 = vld1q_s16(x + q * step), x1 = vld1q_s16(x + q * step + 8);
          a[q][0] = vmlal_s16(a[q][0], vget_low_s16(x0), vget_low_s16(w0));
          a[q][1] = vmlal_high_s16(a[q][1], x0, w0);
          a[q][2] = vmlal_s16(a[q][2], vget_low_s16(x1), vget_low_s16(w1));
          a[q][3] = vmlal_high_s16(a[q][3], x1, w1);
        }
      }
      for (int q = 0; q < 4; q++)
        dw_store(c->jy + (size_t)(t + q) * c->sy + ch, l, ch, a[q]);
    }
    for (; t < t1; t++) {
      const int16_t *x = st + (size_t)(t - t0) * step + g, *w = wd + g;
      int32x4_t a[4] = {b0, b1, b2, b3};
      for (unsigned j = 0; j < taps; j++, w += 64, x += tapstep) {
        int16x8_t w0 = vld1q_s16(w), w1 = vld1q_s16(w + 8);
        int16x8_t x0 = vld1q_s16(x), x1 = vld1q_s16(x + 8);
        a[0] = vmlal_s16(a[0], vget_low_s16(x0), vget_low_s16(w0));
        a[1] = vmlal_high_s16(a[1], x0, w0);
        a[2] = vmlal_s16(a[2], vget_low_s16(x1), vget_low_s16(w1));
        a[3] = vmlal_high_s16(a[3], x1, w1);
      }
      dw_store(c->jy + (size_t)t * c->sy + ch, l, ch, a);
    }
  }
}
/* Residual add: each branch requantised and saturated to int16 separately,
 * saturating int16 sum, then saturation to [0,255] (equal to clamping the
 * exact int32 sum, since [0,255] lies inside int16). */
static void add_rows(qnrt *c, unsigned rank, unsigned u) {
  (void)rank;
  const layer *l = c->jl;
  unsigned t1 = umin(c->jt, (u + 1) * ROW_TCHUNK), C16 = up(l->d[4], 16);
  for (unsigned t = u * ROW_TCHUNK; t < t1; t++) {
    const int8_t *a = (const int8_t *)c->ja + (size_t)t * c->sa,
                 *b = (const int8_t *)c->jb + (size_t)t * c->sb;
    uint8_t *y = c->jy + (size_t)t * c->sy;
    for (unsigned o = 0; o < C16; o += 8) {
      int16x8_t x = vmovl_s8(vld1_s8(a + o)), z = vmovl_s8(vld1_s8(b + o));
      int16x8_t qa = vcombine_s16(
          rq4h(vmovl_s16(vget_low_s16(x)), l->m[0] + o, l->n[0] + o),
          rq4h(vmovl_high_s16(x), l->m[0] + o + 4, l->n[0] + o + 4));
      int16x8_t qb = vcombine_s16(
          rq4h(vmovl_s16(vget_low_s16(z)), l->m[1] + o, l->n[1] + o),
          rq4h(vmovl_high_s16(z), l->m[1] + o + 4, l->n[1] + o + 4));
      vst1_u8(y + o, vqmovun_s16(vqaddq_s16(qa, qb)));
    }
  }
}
#endif
static void run_units(qnrt *c, unsigned rank) {
  const layer *l = c->jl;
  void (*fn)(qnrt *, unsigned, unsigned) = conv_rows;
#ifdef QNRT_NEON
  fn = l->kind == PW ? pw_unit : l->kind == DW ? dw_unit : l->kind == ADD
                                                               ? add_rows
                                                               : conv_rows;
#else
  if (l->kind == ADD)
    fn = add_rows;
#endif
  unsigned a = (uint64_t)c->units * rank / c->nt,
           b = (uint64_t)c->units * (rank + 1) / c->nt;
  for (unsigned u = a; u < b && !atomic_load_explicit(&c->error,
                                                     memory_order_relaxed);
       u++)
    fn(c, rank, u);
}
static inline void relax(void) {
#if defined(__aarch64__)
  __asm__ volatile("yield");
#endif
}
typedef struct {
  qnrt *c;
  unsigned rank;
} start;
/* Workers spin briefly on the job generation (layers arrive back to back),
 * then sleep on the condition variable. */
static void *worker(void *p) {
  start s = *(start *)p;
  free(p);
  qnrt *c = s.c;
  unsigned seen = 0;
  for (;;) {
    unsigned g, spins = 0;
    while ((g = atomic_load(&c->gen)) == seen && !atomic_load(&c->stop)) {
      if (++spins < SPIN) {
        relax();
        continue;
      }
      pthread_mutex_lock(&c->mu);
      atomic_fetch_add(&c->sleepers, 1);
      while (atomic_load(&c->gen) == seen && !atomic_load(&c->stop))
        pthread_cond_wait(&c->cv, &c->mu);
      atomic_fetch_sub(&c->sleepers, 1);
      pthread_mutex_unlock(&c->mu);
    }
    if (atomic_load(&c->stop))
      break;
    seen = g;
    run_units(c, s.rank);
    atomic_fetch_sub(&c->pending, 1);
  }
  return NULL;
}
static void dispatch(qnrt *c) {
  atomic_store(&c->pending, c->created);
  atomic_fetch_add(&c->gen, 1);
  if (atomic_load(&c->sleepers)) {
    pthread_mutex_lock(&c->mu);
    pthread_cond_broadcast(&c->cv);
    pthread_mutex_unlock(&c->mu);
  }
  run_units(c, 0);
  while (atomic_load(&c->pending))
    relax();
}
/* Execute descriptor i on time-major buffers. */
static int exec(qnrt *c, unsigned i, const uint8_t *a, unsigned sa,
                const uint8_t *b, unsigned sb, uint8_t *y, unsigned sy,
                unsigned t, unsigned v, unsigned *ot, unsigned *ov) {
  const layer *l = c->l + i;
  int n = l->d[0] == 1 ? length(t, l) : (int)t;
  int valid = l->d[0] == 1 ? length(v, l) : (int)v;
  if (n < 1 || (unsigned)n > c->max || valid < 0)
    return -1;
  c->jl = l;
  c->ja = a;
  c->jb = b;
  c->jy = y;
  c->sa = sa;
  c->sb = sb;
  c->sy = sy;
  c->jt = t;
  c->jv = v;
  c->jot = n;
  unsigned rows = (n + ROW_TCHUNK - 1) / ROW_TCHUNK;
  c->units = rows;
#ifdef QNRT_NEON
  if (l->kind == PW)
    c->units = (up(l->d[4], 4) + PW_OCHUNK - 1) / PW_OCHUNK *
               ((n + PW_TCHUNK - 1) / PW_TCHUNK);
  else if (l->kind == DW)
    c->units = (up(l->d[4], 16) + 63) / 64 * ((n + DW_TCHUNK - 1) / DW_TCHUNK);
#endif
  atomic_store(&c->error, 0);
  dispatch(c);
  *ot = n;
  *ov = valid;
  return atomic_load(&c->error) ? -2 : 0;
}
static void to_tm(const uint8_t *src, unsigned ch, unsigned T, uint8_t *dst,
                  unsigned S) {
  for (unsigned t = 0; t < T; t++)
    for (unsigned k = 0; k < ch; k++)
      dst[(size_t)t * S + k] = src[(size_t)k * T + t];
}
static void from_tm(const uint8_t *src, unsigned S, unsigned ch, unsigned T,
                    uint8_t *dst) {
  for (unsigned k = 0; k < ch; k++)
    for (unsigned t = 0; t < T; t++)
      dst[(size_t)k * T + t] = src[(size_t)t * S + k];
}
void qnrt_free(qnrt *c) {
  if (!c)
    return;
  pthread_mutex_lock(&c->mu);
  atomic_store(&c->stop, 1);
  pthread_cond_broadcast(&c->cv);
  pthread_mutex_unlock(&c->mu);
  for (unsigned i = 0; i < c->created; i++)
    pthread_join(c->th[i], NULL);
  for (unsigned i = 0; i < c->n; i++) {
    layer *l = c->l + i;
    free(l->w);
    free(l->wp);
    free(l->wd);
    free(l->bias);
    free(l->padrow);
    for (int j = 0; j < 4; j++)
      free(l->p[j]);
    for (int j = 0; j < 2; j++) {
      free(l->m[j]);
      free(l->n[j]);
    }
  }
  if (c->pool)
    for (unsigned i = 0; i < c->npool; i++)
      free(c->pool[i]);
  for (int i = 0; i < 3; i++)
    free(c->scr[i]);
  for (int i = 0; i < 4; i++)
    free(c->stage[i]);
  free(c->pool);
  free(c->logits);
  free(c->ts);
  free(c->vs);
  free(c->slot);
  free(c->l);
  pthread_mutex_destroy(&c->mu);
  pthread_cond_destroy(&c->cv);
  free(c);
}
static void rqparam(int32_t m, int32_t s, int32_t *mo, int64_t *no) {
  int64_t n = 31LL + s;
  *mo = n >= 64 ? 0 : m;
  *no = n >= 64 ? -1 : -n;
}
int qnrt_load(qnrt **out, const char *path, unsigned max, unsigned nt) {
  *out = NULL;
  if (!max || max > (1u << 24) || nt < 1 || nt > 4)
    return -1;
  FILE *f = fopen(path, "rb");
  if (!f)
    return -1;
  fseek(f, 0, SEEK_END);
  long size = ftell(f);
  rewind(f);
  if (size < 64 || size > UINT32_MAX) {
    fclose(f);
    return -1;
  }
  uint8_t *b = malloc(size);
  if (!b) {
    fclose(f);
    return -1;
  }
  int ok = fread(b, 1, size, f) == (size_t)size;
  fclose(f);
  qnrt *c = NULL;
  unsigned *last = NULL;
#define CHECK(x)                                                               \
  do {                                                                         \
    if (!(x))                                                                  \
      goto fail;                                                               \
  } while (0)
  CHECK(ok && !memcmp(b, "QNW1", 4) && rd(b + 4) == 1 && rd(b + 8) == 64 &&
        rd(b + 16) == 128 && rd(b + 20) == 64 &&
        rd(b + 24) == (unsigned long)size && !rd(b + 28));
  for (int i = 36; i < 64; i++)
    CHECK(!b[i]);
  unsigned n = rd(b + 12);
  CHECK(n && n <= ((unsigned long)size - 64) / 128);
  float scale;
  uint32_t sb = rd(b + 32);
  memcpy(&scale, &sb, 4);
  CHECK(isfinite(scale) && scale > 0);
  c = calloc(1, sizeof(*c));
  CHECK(c);
  pthread_mutex_init(&c->mu, NULL);
  pthread_cond_init(&c->cv, NULL);
  c->max = max;
  c->nt = nt;
  c->scale = scale;
  c->l = calloc(n, sizeof(layer));
  CHECK(c->l);
  c->n = n;
  c->ts = calloc(n + 1, sizeof(unsigned));
  c->vs = calloc(n + 1, sizeof(unsigned));
  c->slot = calloc(n + 1, sizeof(unsigned));
  last = calloc(n + 1, sizeof(unsigned));
  CHECK(c->ts && c->vs && c->slot && last);
  c->ts[0] = max;
  size_t poolsz = (size_t)64 * (max + ROWPAD), scrsz = poolsz, stagesz = 0;
  for (unsigned i = 0; i < n; i++) {
    layer *l = c->l + i;
    uint32_t *d = l->d;
    for (int j = 0; j < 32; j++)
      d[j] = rd(b + 64 + 128 * i + 4 * j);
    CHECK((d[0] == 1 || d[0] == 2) && d[1] <= i && d[2] <= i && d[3] <= 1 &&
          d[4] && d[4] <= 65536);
    for (int j = 20; j < 32; j++)
      CHECK(!d[j]);
    unsigned cin = d[1] ? c->l[d[1] - 1].d[4] : 64;
    l->cin = cin;
    l->uns = d[1] ? c->l[d[1] - 1].d[3] : 0;
    last[d[1]] = i;
    if (d[0] == 1) {
      CHECK(!d[2] && !d[11] && d[5] && d[6] && d[6] <= 65536 && d[7] &&
            d[7] <= 65536 && d[8] && d[8] <= 65536 && d[9] <= 65536);
      CHECK((d[10] == 1 && d[5] == cin) ||
            (d[10] == cin && d[4] == cin && d[5] == 1));
      CHECK((uint64_t)d[5] * d[6] <= 1048576);
      l->kind = d[6] == 1 && d[10] == 1 && d[7] == 1 && !d[9] ? PW
                : d[10] == cin && d[10] > 1                  ? DW
                                                             : GEN;
    } else {
      CHECK(d[3] == 1 && d[1] && d[2] && d[4] == cin &&
            c->l[d[2] - 1].d[4] == cin && !l->uns && !c->l[d[2] - 1].d[3]);
      for (int j = 5; j < 12; j++)
        CHECK(!d[j]);
      CHECK(c->ts[d[1]] == c->ts[d[2]]);
      l->kind = ADD;
      last[d[2]] = i;
    }
#ifndef QNRT_NEON
    if (d[0] == 1)
      l->kind = GEN;
#endif
    unsigned O = d[4], P = up(O, 64), K = d[0] == 1 ? d[5] * d[6] : 0;
    unsigned PK = (K + 15) / 16 * 16, woff = 0;
    for (int j = 0; j < 4; j++) {
      unsigned off = d[12 + 2 * j], len = d[13 + 2 * j];
      uint64_t expect = (uint64_t)O * 4;
      if (d[0] == 1 && j == 0)
        expect = ((uint64_t)O + 31) / 32 * 32 * PK;
      CHECK(off % 64 == 0 && off >= 64 + 128 * n &&
            off <= (unsigned long)size && len == expect &&
            len <= (unsigned long)size - off);
      if (d[0] == 1 && j == 0) {
        woff = off;
        continue;
      }
      int32_t *p = malloc((size_t)P * 4);
      CHECK(p);
      l->p[d[0] == 1 ? j - 1 : j] = p;
      for (unsigned o = 0; o < P; o++)
        p[o] = o < O ? (int32_t)rd(b + off + 4 * o) : 0;
    }
#define WB(o, k) ((int8_t)b[woff + (size_t)((o) / 32) * PK * 32 + (k) * 32 + (o) % 32])
    if (d[0] == 1) {
      for (unsigned o = 0; o < (O + 31) / 32 * 32; o++)
        for (unsigned k = 0; k < PK; k++) {
          int8_t w = WB(o, k);
          CHECK(w != -128);
          if (o >= O || k >= K)
            CHECK(!w);
        }
      for (unsigned o = 0; o < O; o++) {
        CHECK(l->p[1][o] > 0 && l->p[2][o] >= -30);
        int64_t low = l->p[0][o], high = low;
        for (unsigned k = 0; k < K; k++) {
          int w = WB(o, k);
          low +=
              (int64_t)w * (w < 0 ? (l->uns ? 255 : 127) : (l->uns ? 0 : -128));
          high +=
              (int64_t)w * (w < 0 ? (l->uns ? 0 : -128) : (l->uns ? 255 : 127));
        }
        CHECK(low >= INT32_MIN && high <= INT32_MAX);
      }
    } else
      for (unsigned o = 0; o < O; o++)
        CHECK(l->p[0][o] > 0 && l->p[2][o] > 0 && l->p[1][o] >= -30 &&
              l->p[3][o] >= -30);
    /* Derived execution parameters. */
    for (int j = 0; j < (d[0] == 1 ? 1 : 2); j++) {
      l->m[j] = calloc(P, sizeof(int32_t));
      l->n[j] = calloc(P, sizeof(int64_t));
      CHECK(l->m[j] && l->n[j]);
      const int32_t *mm = d[0] == 1 ? l->p[1] : l->p[2 * j],
                    *ss = d[0] == 1 ? l->p[2] : l->p[2 * j + 1];
      for (unsigned o = 0; o < P; o++)
        if (o < O)
          rqparam(mm[o], ss[o], l->m[j] + o, l->n[j] + o);
        else
          l->n[j][o] = -1;
    }
    if (l->kind == GEN) {
      l->w = malloc((size_t)O * K);
      CHECK(l->w);
      for (unsigned o = 0; o < O; o++)
        for (unsigned k = 0; k < K; k++)
          l->w[(size_t)o * K + k] = WB(o, k);
    } else if (l->kind == PW) {
      unsigned steps = PK / 16;
      l->wp = calloc((size_t)up(O, 4) / 4 * steps * 64 + 64, 1);
      l->bias = calloc(P, sizeof(int32_t));
      l->padrow = calloc(P, 1);
      CHECK(l->wp && l->bias && l->padrow);
      for (unsigned o = 0; o < O; o++) {
        int64_t sum = 0;
        for (unsigned k = 0; k < K; k++) {
          int8_t w = WB(o, k);
          sum += w;
          l->wp[((size_t)o / 4 * steps + k / 16) * 64 + o % 4 * 16 + k % 16] = w;
        }
        int64_t z = l->p[0][o] + (l->uns ? 128 * sum : 0);
        l->bias[o] = (int32_t)(uint32_t)(uint64_t)z; /* modular, see docs */
        l->padrow[o] = (uint8_t)rq(l->p[0][o], l->p[1][o], l->p[2][o],
                                   d[3] ? 0 : -128, d[3] ? 255 : 127);
      }
    } else if (l->kind == DW) {
      unsigned taps = d[6], slices = (up(O, 16) + 63) / 64;
      l->wd = calloc((size_t)slices * taps * 64, sizeof(int16_t));
      l->bias = calloc(P, sizeof(int32_t));
      CHECK(l->wd && l->bias);
      for (unsigned o = 0; o < O; o++) {
        l->bias[o] = l->p[0][o];
        for (unsigned j = 0; j < taps; j++)
          l->wd[((size_t)o / 64 * taps + j) * 64 + o % 64] = WB(o, j);
      }
      size_t rows = (size_t)(DW_TCHUNK - 1) * d[7] + (size_t)(d[6] - 1) * d[8] + 1;
      if (rows * 64 * sizeof(int16_t) > stagesz)
        stagesz = rows * 64 * sizeof(int16_t);
    }
#undef WB
    int t = d[0] == 1 ? length(c->ts[d[1]], l) : (int)c->ts[d[1]];
    CHECK(t > 0 && (unsigned)t <= max);
    c->ts[i + 1] = t;
    size_t need = (size_t)stride_of(O) * (t + ROWPAD);
    if (need > poolsz)
      poolsz = need;
    need = (size_t)stride_of(O > cin ? O : cin) * (max + ROWPAD);
    if (need > scrsz)
      scrsz = need;
  }
  /* Liveness: a tensor's buffer returns to the free list after its last
   * consumer; outputs never alias the inputs of their own layer. */
  c->pool = calloc(n + 1, sizeof(uint8_t *));
  unsigned *freelist = calloc(n + 1, sizeof(unsigned)), nfree = 0;
  CHECK(c->pool && freelist);
  c->slot[0] = c->npool++;
  last[n] = n;
  for (unsigned i = 0; i < n; i++) {
    c->slot[i + 1] = nfree ? freelist[--nfree] : c->npool++;
    for (unsigned k = 0; k <= i; k++)
      if (last[k] == i)
        freelist[nfree++] = c->slot[k];
  }
  free(freelist);
  for (unsigned i = 0; i < c->npool; i++) {
    c->pool[i] = calloc(poolsz, 1);
    CHECK(c->pool[i]);
  }
  for (int i = 0; i < 3; i++) {
    c->scr[i] = calloc(scrsz, 1);
    CHECK(c->scr[i]);
  }
  for (unsigned i = 0; i < nt; i++) {
    c->stage[i] = malloc(stagesz ? stagesz : 64);
    CHECK(c->stage[i]);
  }
  c->logits = malloc((size_t)c->l[n - 1].d[4] * c->ts[n]);
  CHECK(c->logits);
  for (unsigned r = 1; r < nt; r++) {
    start *s = malloc(sizeof(*s));
    CHECK(s);
    s->c = c;
    s->rank = r;
    if (pthread_create(c->th + c->created, NULL, worker, s)) {
      free(s);
      goto fail;
    }
    c->created++;
  }
  free(last);
  free(b);
  *out = c;
  return 0;
fail:
  free(last);
  free(b);
  qnrt_free(c);
  return -1;
#undef CHECK
}
float qnrt_input_scale(const qnrt *c) { return c->scale; }
unsigned qnrt_layers(const qnrt *c) { return c->n; }
void qnrt_profile(qnrt *c, double *seconds) { c->prof = seconds; }
/* Propagate allocated lengths through the graph; optional MAC total. */
static unsigned walk(const qnrt *c, unsigned frames, uint64_t *macs) {
  unsigned *len = malloc((c->n + 1) * sizeof(unsigned)), r;
  if (!len)
    return 0;
  len[0] = frames;
  for (unsigned i = 0; i < c->n; i++) {
    const layer *l = c->l + i;
    int x = l->d[0] == 1 ? length(len[l->d[1]], l) : (int)len[l->d[1]];
    len[i + 1] = x < 0 ? 0 : x;
    if (macs && l->d[0] == 1)
      *macs += (uint64_t)l->d[4] * l->d[5] * l->d[6] * len[i + 1];
  }
  r = len[c->n];
  free(len);
  return r;
}
unsigned qnrt_output_frames(const qnrt *c, unsigned frames) {
  return walk(c, frames, NULL);
}
uint64_t qnrt_macs(const qnrt *c, unsigned frames) {
  uint64_t total = 0;
  walk(c, frames, &total);
  return total;
}
int qnrt_layer_kind(const qnrt *c, unsigned i) {
  if (i >= c->n)
    return -1;
  const uint32_t *d = c->l[i].d;
  return d[0] == 2 ? QNRT_ADD
         : d[6] == 1 && d[10] == 1 ? QNRT_POINTWISE
         : d[10] > 1               ? QNRT_DEPTHWISE
                                   : QNRT_CONV;
}
int qnrt_layer(qnrt *c, unsigned i, const void *a, const void *b, unsigned t,
               unsigned v, void *out, unsigned *ot, unsigned *ov) {
  if (i >= c->n || !a || !out || !t || t > c->max || v > t)
    return -1;
  const layer *l = c->l + i;
  if (l->d[0] == 2 && !b)
    return -1;
  unsigned sa = stride_of(l->cin), sy = stride_of(l->d[4]), n, valid;
  to_tm(a, l->cin, t, c->scr[0], sa);
  if (l->d[0] == 2)
    to_tm(b, l->cin, t, c->scr[1], sa);
  int e = exec(c, i, c->scr[0], sa, c->scr[1], sa, c->scr[2], sy, t, v, &n,
               &valid);
  if (e)
    return e;
  from_tm(c->scr[2], sy, l->d[4], n, out);
  *ot = n;
  *ov = valid;
  return 0;
}
static uint8_t *tensor(qnrt *c, unsigned k) { return c->pool[c->slot[k]]; }
static unsigned tstride(const qnrt *c, unsigned k) {
  return k ? stride_of(c->l[k - 1].d[4]) : 64;
}
static double now(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return t.tv_sec + t.tv_nsec * 1e-9;
}
static int run_graph(qnrt *c, unsigned t, unsigned v, const int8_t **y,
                     unsigned *ot, unsigned *ov) {
  c->ts[0] = t;
  c->vs[0] = v;
  for (unsigned i = 0; i < c->n; i++) {
    const layer *l = c->l + i;
    unsigned a = l->d[1], b = l->d[2];
    if (l->d[0] == 2 && (c->vs[a] != c->vs[b] || c->ts[a] != c->ts[b]))
      return -1;
    double s = c->prof ? now() : 0;
    int e = exec(c, i, tensor(c, a), tstride(c, a), tensor(c, b),
                 tstride(c, b), tensor(c, i + 1), tstride(c, i + 1), c->ts[a],
                 c->vs[a], c->ts + i + 1, c->vs + i + 1);
    if (e)
      return e;
    if (c->prof)
      c->prof[i] = now() - s;
  }
  unsigned C = c->l[c->n - 1].d[4];
  from_tm(tensor(c, c->n), tstride(c, c->n), C, c->ts[c->n],
          (uint8_t *)c->logits);
  *y = c->logits;
  *ot = c->ts[c->n];
  *ov = c->vs[c->n];
  return 0;
}
int qnrt_run(qnrt *c, const int8_t *x, unsigned t, unsigned v, const int8_t **y,
             unsigned *ot, unsigned *ov) {
  if (!x || !t || t > c->max || v > t)
    return -1;
  to_tm((const uint8_t *)x, 64, t, tensor(c, 0), 64);
  return run_graph(c, t, v, y, ot, ov);
}
int qnrt_run_frames(qnrt *c, const int8_t *x, unsigned t, unsigned v,
                    const int8_t **y, unsigned *ot, unsigned *ov) {
  if (!x || !t || t > c->max || v > t)
    return -1;
  memcpy(tensor(c, 0), x, (size_t)64 * t);
  return run_graph(c, t, v, y, ot, ov);
}
