// Runner on the four A53s with NEON, for the pointwise, depthwise and residual add layers
// Activations are frame-major, one row of channels padded to 64 bytes per frame
#include "runner.h"
#include <arm_neon.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define THREADS 4
#define ROWPAD 8 // spare rows so the 4-frame kernels can read past the last frame
#define PW_O 64 // pointwise work unit of 64 output channels
#define PW_T 16 // by 16 frames
#define DW_T 64 // depthwise work unit of 64 channels by 64 frames
#define ADD_T 16 // add work unit of 16 frames

enum { PW, DW, ADD };

typedef struct {
    unsigned kind, in[2], relu, uns, channels, cin, k, taps, stride, dilation, padding;
    int8_t* wp;
    int16_t* wd;
    int32_t* b;
    int32_t* bias;
    int32_t* m[2];
    int64_t* n[2];
    uint8_t* padrow;
} layer;

static struct {
    unsigned count, slots;
    layer* l;
    unsigned *frames, *valid, *slot;
    uint8_t** pool;
    int16_t* stage[THREADS];
} net;

// One thread's share of one layer
typedef struct {
    const layer* l;
    const uint8_t *a, *b;
    uint8_t* y;
    unsigned sa, sb, sy, frames, valid, out_frames, units, rank;
} job;

static unsigned up(unsigned x, unsigned m) {
    return (x + m - 1) / m * m;
}

static unsigned umin(unsigned a, unsigned b) {
    return a < b ? a : b;
}

static void* alloc(size_t size) {
    void* p = calloc(1, size);
    if (!p) {
        fputs("Out of memory\n", stderr);
        exit(1);
    }
    return p;
}

static unsigned conv_frames(const layer* l, unsigned t) {
    return (t + 2 * l->padding - l->dilation * (l->taps - 1) - 1) / l->stride + 1;
}

// Requantise four lanes, where adding p >> 63 turns SRSHL's round half up into round half away from zero
static inline int32x4_t rq4(int32x4_t a, int32x4_t m, int64x2_t nlo, int64x2_t nhi) {
    int64x2_t lo = vmull_s32(vget_low_s32(a), vget_low_s32(m));
    int64x2_t hi = vmull_high_s32(a, m);
    lo = vrshlq_s64(vsraq_n_s64(lo, lo, 63), nlo);
    hi = vrshlq_s64(vsraq_n_s64(hi, hi, 63), nhi);
    return vcombine_s32(vqmovn_s64(lo), vqmovn_s64(hi));
}

static inline int16x4_t rq4h(int32x4_t a, const int32_t* m, const int64_t* n) {
    return vqmovn_s32(rq4(a, vld1q_s32(m), vld1q_s64(n), vld1q_s64(n + 2)));
}

// The saturating narrows equal one clamp to [0, 255] or [-128, 127]
static inline uint8x8_t narrow8(int16x8_t h, int relu) {
    return relu ? vqmovun_s16(h) : vreinterpret_u8_s8(vqmovn_s16(h));
}

// 4 output channels by 4 frames and 16 k per step, with int16 pair sums of at most 32512 folded by SADALP
// The next step's loads read past the padded operands, and EOR 0x80 offsets unsigned input by -128
// clang-format off
#define SM(t, w, x) "smull " t ".8h, " w ".8b, " x ".8b\n"
#define SM2(t, w, x) "smlal2 " t ".8h, " w ".16b, " x ".16b\n"
#define SA(a, t) "sadalp %[" a "].4s, " t ".8h\n"
#define MUL4(w, a, b, c, d) SM(a, w, "v20") SM(b, w, "v21") SM(c, w, "v22") SM(d, w, "v23")
#define MLA4(w, a, b, c, d) SM2(a, w, "v20") SM2(b, w, "v21") SM2(c, w, "v22") SM2(d, w, "v23")
#define ACC4(i, j, k, l, a, b, c, d) SA(i, a) SA(j, b) SA(k, c) SA(l, d)
// Output-only accumulators keep GCC below its 30-operand limit
#define PW_INIT                                                                     \
    "movi %[a0].4s, #0\n"  "movi %[a1].4s, #0\n"  "movi %[a2].4s, #0\n"  "movi %[a3].4s, #0\n"  \
    "movi %[a4].4s, #0\n"  "movi %[a5].4s, #0\n"  "movi %[a6].4s, #0\n"  "movi %[a7].4s, #0\n"  \
    "movi %[a8].4s, #0\n"  "movi %[a9].4s, #0\n"  "movi %[a10].4s, #0\n" "movi %[a11].4s, #0\n" \
    "movi %[a12].4s, #0\n" "movi %[a13].4s, #0\n" "movi %[a14].4s, #0\n" "movi %[a15].4s, #0\n"
#define PW_PRE                                                                      \
    "ld1 {v16.16b, v17.16b, v18.16b, v19.16b}, [%[w]], #64\n"                       \
    "ldr q20, [%[x0]], #16\n" "ldr q21, [%[x1]], #16\n"                             \
    "ldr q22, [%[x2]], #16\n" "ldr q23, [%[x3]], #16\n"                             \
    "1:\n"
#define PW_EOR                                                                      \
    "movi v24.16b, #0x80\n"                                                         \
    "eor v20.16b, v20.16b, v24.16b\n" "eor v21.16b, v21.16b, v24.16b\n"             \
    "eor v22.16b, v22.16b, v24.16b\n" "eor v23.16b, v23.16b, v24.16b\n"
#define PW_BODY                                                                     \
    MUL4("v16", "v24", "v25", "v26", "v27") MLA4("v16", "v24", "v25", "v26", "v27") \
    "ldr q16, [%[w]], #16\n"                                                        \
    MUL4("v17", "v28", "v29", "v30", "v31")                                         \
    ACC4("a0", "a1", "a2", "a3", "v24", "v25", "v26", "v27")                        \
    MLA4("v17", "v28", "v29", "v30", "v31")                                         \
    "ldr q17, [%[w]], #16\n"                                                        \
    MUL4("v18", "v24", "v25", "v26", "v27")                                         \
    ACC4("a4", "a5", "a6", "a7", "v28", "v29", "v30", "v31")                        \
    MLA4("v18", "v24", "v25", "v26", "v27")                                         \
    "ldr q18, [%[w]], #16\n"                                                        \
    MUL4("v19", "v28", "v29", "v30", "v31")                                         \
    ACC4("a8", "a9", "a10", "a11", "v24", "v25", "v26", "v27")                      \
    MLA4("v19", "v28", "v29", "v30", "v31")                                         \
    "ldr q19, [%[w]], #16\n"                                                        \
    "ldr q20, [%[x0]], #16\n" SA("a12", "v28")                                      \
    "ldr q21, [%[x1]], #16\n" SA("a13", "v29")                                      \
    "ldr q22, [%[x2]], #16\n" SA("a14", "v30")                                      \
    "ldr q23, [%[x3]], #16\n" SA("a15", "v31")                                      \
    "subs %w[k], %w[k], #1\n"                                                       \
    "b.ne 1b\n"
#define PW_OPS                                                                      \
    : [a0] "=&w"(a0), [a1] "=&w"(a1), [a2] "=&w"(a2), [a3] "=&w"(a3),               \
      [a4] "=&w"(a4), [a5] "=&w"(a5), [a6] "=&w"(a6), [a7] "=&w"(a7),               \
      [a8] "=&w"(a8), [a9] "=&w"(a9), [a10] "=&w"(a10), [a11] "=&w"(a11),           \
      [a12] "=&w"(a12), [a13] "=&w"(a13), [a14] "=&w"(a14), [a15] "=&w"(a15),       \
      [w] "+r"(w), [x0] "+r"(x0), [x1] "+r"(x1), [x2] "+r"(x2), [x3] "+r"(x3),      \
      [k] "+r"(k)                                                                   \
    :                                                                               \
    : "v16", "v17", "v18", "v19", "v20", "v21", "v22", "v23",                       \
      "v24", "v25", "v26", "v27", "v28", "v29", "v30", "v31", "cc", "memory"
// clang-format on

// r[t] holds output channels 0 to 3 of frame t
static inline void pw_block(const int8_t* w, const uint8_t* x, size_t stride, unsigned k, int uns, int32x4_t r[4]) {
    int32x4_t a0, a1, a2, a3, a4, a5, a6, a7, a8, a9, a10, a11, a12, a13, a14, a15;
    const uint8_t *x0 = x, *x1 = x + stride, *x2 = x + 2 * stride, *x3 = x + 3 * stride;
    if (uns) {
        __asm__(PW_INIT PW_PRE PW_EOR PW_BODY PW_OPS);
    }
    else {
        __asm__(PW_INIT PW_PRE PW_BODY PW_OPS);
    }
    r[0] = vpaddq_s32(vpaddq_s32(a0, a4), vpaddq_s32(a8, a12));
    r[1] = vpaddq_s32(vpaddq_s32(a1, a5), vpaddq_s32(a9, a13));
    r[2] = vpaddq_s32(vpaddq_s32(a2, a6), vpaddq_s32(a10, a14));
    r[3] = vpaddq_s32(vpaddq_s32(a3, a7), vpaddq_s32(a11, a15));
}

// Pointwise of 64 output channels by 16 frames. Masked frames take the precomputed padrow
static void pw_unit(const job* jb, unsigned u) {
    const layer* l = jb->l;
    unsigned blocks = (jb->frames + PW_T - 1) / PW_T, steps = (l->cin + 15) / 16;
    unsigned o0 = u / blocks * PW_O, o1 = umin(o0 + PW_O, up(l->channels, 4));
    unsigned t0 = u % blocks * PW_T, t1 = umin(t0 + PW_T, jb->frames);
    for (unsigned o = o0; o < o1; o += 4) {
        const int8_t* w = l->wp + (size_t)o / 4 * steps * 64;
        int32x4_t bias = vld1q_s32(l->bias + o), m = vld1q_s32(l->m[0] + o);
        int64x2_t nlo = vld1q_s64(l->n[0] + o), nhi = vld1q_s64(l->n[0] + o + 2);
        for (unsigned t = t0; t < umin(t1, up(jb->valid, 4)); t += 4) {
            int32x4_t r[4];
            pw_block(w, jb->a + (size_t)t * jb->sa, jb->sa, steps, l->uns, r);
            for (int j = 0; j < 4; j++) {
                int16x4_t h = vqmovn_s32(rq4(vaddq_s32(r[j], bias), m, nlo, nhi));
                uint8x8_t b = narrow8(vcombine_s16(h, h), l->relu);
                vst1_lane_u32((uint32_t*)(jb->y + (size_t)(t + j) * jb->sy + o), vreinterpret_u32_u8(b), 0);
            }
        }
    }
    for (unsigned t = t0 > jb->valid ? t0 : jb->valid; t < t1; t++) {
        memcpy(jb->y + (size_t)t * jb->sy + o0, l->padrow + o0, o1 - o0);
    }
}

static inline void dw_store(uint8_t* y, const layer* l, unsigned ch, const int32x4_t a[4]) {
    int16x4_t h0 = rq4h(a[0], l->m[0] + ch, l->n[0] + ch);
    int16x4_t h1 = rq4h(a[1], l->m[0] + ch + 4, l->n[0] + ch + 4);
    int16x4_t h2 = rq4h(a[2], l->m[0] + ch + 8, l->n[0] + ch + 8);
    int16x4_t h3 = rq4h(a[3], l->m[0] + ch + 12, l->n[0] + ch + 12);
    vst1q_u8(y, vcombine_u8(narrow8(vcombine_s16(h0, h1), l->relu), narrow8(vcombine_s16(h2, h3), l->relu)));
}

// Depthwise of 64 channels by up to 64 output frames, from input rows widened to int16 and zero outside valid
// Vectors run along channels, and four output frames share each weight load
static void dw_unit(const job* jb, unsigned u) {
    const layer* l = jb->l;
    unsigned blocks = (jb->out_frames + DW_T - 1) / DW_T, slice = u / blocks;
    unsigned c0 = slice * 64, width = umin(64, up(l->channels, 16) - c0);
    unsigned t0 = u % blocks * DW_T, t1 = umin(t0 + DW_T, jb->out_frames);
    unsigned rows = (t1 - 1 - t0) * l->stride + (l->taps - 1) * l->dilation + 1;
    int64_t r0 = (int64_t)t0 * l->stride - l->padding;
    int16_t* stage = net.stage[jb->rank];
    for (unsigned i = 0; i < rows; i++) {
        int64_t r = r0 + i;
        int16_t* row = stage + (size_t)i * 64;
        if (r < 0 || r >= jb->valid) {
            memset(row, 0, width * sizeof(int16_t));
            continue;
        }
        const uint8_t* src = jb->a + (size_t)r * jb->sa + c0;
        for (unsigned g = 0; g < width; g += 16) {
            uint8x16_t b = vld1q_u8(src + g);
            int16x8_t lo, hi;
            if (l->uns) {
                lo = vreinterpretq_s16_u16(vmovl_u8(vget_low_u8(b)));
                hi = vreinterpretq_s16_u16(vmovl_high_u8(b));
            }
            else {
                lo = vmovl_s8(vget_low_s8(vreinterpretq_s8_u8(b)));
                hi = vmovl_high_s8(vreinterpretq_s8_u8(b));
            }
            vst1q_s16(row + g, lo);
            vst1q_s16(row + g + 8, hi);
        }
    }
    const int16_t* wd = l->wd + (size_t)slice * l->taps * 64;
    size_t step = (size_t)l->stride * 64, tapstep = (size_t)l->dilation * 64;
    for (unsigned g = 0; g < width; g += 16) {
        unsigned ch = c0 + g;
        int32x4_t b0 = vld1q_s32(l->b + ch), b1 = vld1q_s32(l->b + ch + 4);
        int32x4_t b2 = vld1q_s32(l->b + ch + 8), b3 = vld1q_s32(l->b + ch + 12);
        unsigned t = t0;
        for (; t + 4 <= t1; t += 4) {
            const int16_t* x = stage + (size_t)(t - t0) * step + g;
            const int16_t* w = wd + g;
            int32x4_t a[4][4];
            for (int q = 0; q < 4; q++) {
                a[q][0] = b0;
                a[q][1] = b1;
                a[q][2] = b2;
                a[q][3] = b3;
            }
            for (unsigned j = 0; j < l->taps; j++, w += 64, x += tapstep) {
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
            for (int q = 0; q < 4; q++) {
                dw_store(jb->y + (size_t)(t + q) * jb->sy + ch, l, ch, a[q]);
            }
        }
        for (; t < t1; t++) {
            const int16_t* x = stage + (size_t)(t - t0) * step + g;
            const int16_t* w = wd + g;
            int32x4_t a[4] = {b0, b1, b2, b3};
            for (unsigned j = 0; j < l->taps; j++, w += 64, x += tapstep) {
                int16x8_t w0 = vld1q_s16(w), w1 = vld1q_s16(w + 8);
                int16x8_t x0 = vld1q_s16(x), x1 = vld1q_s16(x + 8);
                a[0] = vmlal_s16(a[0], vget_low_s16(x0), vget_low_s16(w0));
                a[1] = vmlal_high_s16(a[1], x0, w0);
                a[2] = vmlal_s16(a[2], vget_low_s16(x1), vget_low_s16(w1));
                a[3] = vmlal_high_s16(a[3], x1, w1);
            }
            dw_store(jb->y + (size_t)t * jb->sy + ch, l, ch, a);
        }
    }
}

// Residual add of both inputs rescaled to int16, then a saturating sum and narrow equal to clamping the exact sum
static void add_rows(const job* jb, unsigned u) {
    const layer* l = jb->l;
    for (unsigned t = u * ADD_T; t < umin(jb->frames, (u + 1) * ADD_T); t++) {
        const int8_t* a = (const int8_t*)jb->a + (size_t)t * jb->sa;
        const int8_t* b = (const int8_t*)jb->b + (size_t)t * jb->sb;
        uint8_t* y = jb->y + (size_t)t * jb->sy;
        for (unsigned o = 0; o < up(l->channels, 16); o += 8) {
            int16x8_t x = vmovl_s8(vld1_s8(a + o)), z = vmovl_s8(vld1_s8(b + o));
            int16x8_t qa = vcombine_s16(rq4h(vmovl_s16(vget_low_s16(x)), l->m[0] + o, l->n[0] + o),
                                        rq4h(vmovl_high_s16(x), l->m[0] + o + 4, l->n[0] + o + 4));
            int16x8_t qb = vcombine_s16(rq4h(vmovl_s16(vget_low_s16(z)), l->m[1] + o, l->n[1] + o),
                                        rq4h(vmovl_high_s16(z), l->m[1] + o + 4, l->n[1] + o + 4));
            vst1_u8(y + o, vqmovun_s16(vqaddq_s16(qa, qb)));
        }
    }
}

static void (*const kernels[])(const job*, unsigned) = {[PW] = pw_unit, [DW] = dw_unit, [ADD] = add_rows};

// Run this thread's contiguous share of the layer's work units
static void* run_units(void* p) {
    const job* jb = p;
    for (unsigned u = jb->units * jb->rank / THREADS; u < jb->units * (jb->rank + 1) / THREADS; u++) {
        kernels[jb->l->kind](jb, u);
    }
    return NULL;
}

static uint8_t* tensor(unsigned k) { return net.pool[net.slot[k]]; }

static unsigned row(unsigned k) { return k ? up(net.l[k - 1].channels, 64) : 64; }

static void run_layer(unsigned i) {
    const layer* l = &net.l[i];
    unsigned a = l->in[0], b = l->in[1], frames = net.frames[a], valid = net.valid[a];
    unsigned out = l->kind == ADD ? frames : conv_frames(l, frames);
    net.frames[i + 1] = out;
    net.valid[i + 1] = l->kind == ADD ? valid : conv_frames(l, valid);
    unsigned units = (out + ADD_T - 1) / ADD_T;
    if (l->kind == PW) {
        units = (up(l->channels, 4) + PW_O - 1) / PW_O * ((out + PW_T - 1) / PW_T);
    }
    if (l->kind == DW) {
        units = (up(l->channels, 16) + 63) / 64 * ((out + DW_T - 1) / DW_T);
    }
    job jobs[THREADS];
    pthread_t threads[THREADS];
    for (unsigned r = 0; r < THREADS; r++) {
        jobs[r] = (job){.l = l, .a = tensor(a), .b = tensor(b), .y = tensor(i + 1), .rank = r};
        jobs[r].sa = row(a);
        jobs[r].sb = row(b);
        jobs[r].sy = row(i + 1);
        jobs[r].frames = frames;
        jobs[r].valid = valid;
        jobs[r].out_frames = out;
        jobs[r].units = units;
    }
    for (unsigned r = 1; r < THREADS; r++) {
        pthread_create(&threads[r], NULL, run_units, &jobs[r]);
    }
    run_units(&jobs[0]);
    for (unsigned r = 1; r < THREADS; r++) {
        pthread_join(threads[r], NULL);
    }
}

// Copy `count` int32 values, zero-padded to `size`
static int32_t* words(const uint8_t* src, unsigned count, unsigned size) {
    int32_t* p = alloc((size_t)size * 4);
    memcpy(p, src, (size_t)count * 4);
    return p;
}

static void requant(layer* l, int j, const uint8_t* mult, const uint8_t* shift, unsigned size) {
    int32_t *m = words(mult, l->channels, size), *s = words(shift, l->channels, size);
    l->m[j] = alloc((size_t)size * 4);
    l->n[j] = alloc((size_t)size * 8);
    for (unsigned o = 0; o < size; o++) {
        int64_t n = 31LL + s[o];
        l->m[j][o] = o < l->channels && n < 64 ? m[o] : 0;
        l->n[j][o] = o < l->channels && n < 64 ? -n : -1;
    }
    free(m);
    free(s);
}

static int load(const char* path) {
    FILE* f = fopen(path, "rb");
    long size = f && !fseek(f, 0, SEEK_END) ? ftell(f) : 0;
    uint8_t* blob = alloc(size > 0 ? size : 1);
    if (f) {
        rewind(f);
        size = (long)fread(blob, 1, size, f);
        fclose(f);
    }
    if (size < 64 || memcmp(blob, "QNW1", 4)) {
        fprintf(stderr, "Cannot read %s\n", path);
        free(blob);
        return -1;
    }

    // QNW1 is a 64-byte header, a 128-byte descriptor per layer, then the arrays (see scripts/convert.py)
    memcpy(&net.count, blob + 12, 4);
    net.l = alloc(net.count * sizeof(layer));
    net.frames = alloc((net.count + 1) * sizeof(unsigned));
    net.valid = alloc((net.count + 1) * sizeof(unsigned));
    net.slot = alloc((net.count + 1) * sizeof(unsigned));
    unsigned* last = alloc((net.count + 1) * sizeof(unsigned)); // last layer reading each tensor
    net.frames[0] = ENGINE_FRAMES;
    size_t poolsize = (size_t)64 * (ENGINE_FRAMES + ROWPAD), stagesize = 64;
    for (unsigned i = 0; i < net.count; i++) {
        uint32_t d[32];
        memcpy(d, blob + 64 + 128 * i, sizeof d);
        layer* l = &net.l[i];
        *l = (layer){.in = {d[1], d[2]}, .relu = d[3], .channels = d[4]};
        l->cin = d[1] ? net.l[d[1] - 1].channels : 64;
        l->uns = d[1] ? net.l[d[1] - 1].relu : 0;
        unsigned O = l->channels, P = up(O, 64);
        last[d[1]] = i;
        if (d[0] == 2) {
            l->kind = ADD;
            last[d[2]] = i;
            requant(l, 0, blob + d[12], blob + d[14], P);
            requant(l, 1, blob + d[16], blob + d[18], P);
            net.frames[i + 1] = net.frames[d[1]];
        }
        else {
            l->k = d[5] * d[6];
            l->taps = d[6];
            l->stride = d[7];
            l->dilation = d[8];
            l->padding = d[9];
            l->b = words(blob + d[14], O, P);
            requant(l, 0, blob + d[16], blob + d[18], P);
            net.frames[i + 1] = conv_frames(l, net.frames[d[1]]);
            l->kind = l->taps == 1 ? PW : DW;
            // Weights are tiled [O / 32][k / 16][16][32]
            unsigned k16 = up(l->k, 16);
            const int8_t* tiled = (const int8_t*)blob + d[12];
#define W(o, k) tiled[(size_t)(o) / 32 * k16 * 32 + (k) * 32 + (o) % 32]
            if (l->kind == PW) {
                l->wp = alloc((size_t)up(O, 4) / 4 * k16 * 4 + 64);
                l->bias = alloc(P * 4);
                l->padrow = alloc(P);
                for (unsigned o = 0; o < O; o++) {
                    int64_t sum = 0;
                    for (unsigned k = 0; k < l->k; k++) {
                        sum += W(o, k);
                        l->wp[((size_t)o / 4 * k16 / 16 + k / 16) * 64 + o % 4 * 16 + k % 16] = W(o, k);
                    }
                    // May wrap, like the accumulator it starts, since the true sum fits in int32
                    l->bias[o] = (int32_t)(uint32_t)(uint64_t)(l->b[o] + (l->uns ? 128 * sum : 0));
                }
                // A masked frame is all zeros, so its output is the requantised bias
                for (unsigned o = 0; o < O; o += 4) {
                    int16x4_t h = rq4h(vld1q_s32(l->b + o), l->m[0] + o, l->n[0] + o);
                    uint8x8_t b = narrow8(vcombine_s16(h, h), l->relu);
                    vst1_lane_u32((uint32_t*)(l->padrow + o), vreinterpret_u32_u8(b), 0);
                }
            }
            else {
                l->wd = alloc((size_t)(up(O, 16) + 63) / 64 * l->taps * 64 * sizeof(int16_t));
                for (unsigned o = 0; o < O; o++) {
                    for (unsigned j = 0; j < l->taps; j++) {
                        l->wd[((size_t)o / 64 * l->taps + j) * 64 + o % 64] = W(o, j);
                    }
                }
                size_t rows = (size_t)(DW_T - 1) * l->stride + (size_t)(l->taps - 1) * l->dilation + 1;
                if (rows * 64 * sizeof(int16_t) > stagesize) {
                    stagesize = rows * 64 * sizeof(int16_t);
                }
            }
#undef W
        }
        size_t need = (size_t)P * (net.frames[i + 1] + ROWPAD);
        if (need > poolsize) {
            poolsize = need;
        }
    }

    // A tensor's buffer is reused after its last reader, and a layer never writes over its own inputs
    unsigned* free_slots = alloc((net.count + 1) * sizeof(unsigned));
    unsigned free_count = 0;
    net.slots = 1;
    last[net.count] = net.count;
    for (unsigned i = 0; i < net.count; i++) {
        net.slot[i + 1] = free_count ? free_slots[--free_count] : net.slots++;
        for (unsigned k = 0; k <= i; k++) {
            if (last[k] == i) {
                free_slots[free_count++] = net.slot[k];
            }
        }
    }
    net.pool = alloc(net.slots * sizeof(uint8_t*));
    for (unsigned i = 0; i < net.slots; i++) {
        net.pool[i] = alloc(poolsize);
    }
    for (unsigned r = 0; r < THREADS; r++) {
        net.stage[r] = alloc(stagesize);
    }
    free(free_slots);
    free(last);
    free(blob);
    return 0;
}

static int run(const int8_t* features, unsigned frames, unsigned valid, int8_t* logits, unsigned* out_valid) {
    uint8_t* x = tensor(0);
    for (unsigned t = 0; t < frames; t++) {
        for (int b = 0; b < FE_BANDS; b++) {
            x[t * FE_BANDS + b] = features[b * frames + t];
        }
    }
    net.frames[0] = frames;
    net.valid[0] = valid;
    for (unsigned i = 0; i < net.count; i++) {
        run_layer(i);
    }
    unsigned n = net.count, out = net.frames[n];
    const uint8_t* y = tensor(n);
    for (unsigned t = 0; t < out; t++) {
        for (int k = 0; k < ENGINE_CLASSES; k++) {
            logits[k * out + t] = y[t * row(n) + k];
        }
    }
    *out_valid = net.valid[n];
    return out;
}

const runner cpu_runner = {load, run};
