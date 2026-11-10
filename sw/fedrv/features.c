#include "features.h"
#include <math.h>

void fe_log_frame(const uint64_t p[64], float z[64]) {
  for (int b = 0; b < 64; b++) z[b] = logf((float)((double)p[b] * 0x1p-42) + 0x1p-24f);
}

/* numpy/_core/src/umath/loops_utils.h.src pairwise_sum for float32. */
static float pairwise(const float *a, size_t n) {
  if (n < 8) {
    float r = 0;
    for (size_t i = 0; i < n; i++) r += a[i];
    return r;
  }
  if (n <= 128) {
    float r[8];
    size_t i;
    for (int j = 0; j < 8; j++) r[j] = a[j];
    for (i = 8; i < n - n % 8; i += 8)
      for (int j = 0; j < 8; j++) r[j] += a[i + j];
    float s = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
    for (; i < n; i++) s += a[i];
    return s;
  }
  size_t n2 = n / 2;
  n2 -= n2 % 8;
  return pairwise(a, n2) + pairwise(a + n2, n - n2);
}

int fe_normalize_utterance(float *z, size_t T) {
  if (T < 2) return -1;
  float sq[8192];
  if (T > sizeof(sq) / sizeof(sq[0])) return -1; /* NumPy buffers reductions above this */
  for (int b = 0; b < 64; b++) {
    float *x = z + (size_t)b * T;
    /* z.mean / z.std(ddof=1): float32 sums, divide by an intp count in float64. */
    float mean = (float)((double)(0.0f + pairwise(x, T)) / (double)T);
    for (size_t t = 0; t < T; t++) {
      float d = x[t] - mean;
      sq[t] = d * d;
    }
    float std = sqrtf((float)((double)(0.0f + pairwise(sq, T)) / (double)(T - 1)));
    float den = std + 1e-5f;
    for (size_t t = 0; t < T; t++) x[t] = (x[t] - mean) / den;
  }
  return 0;
}

void fe_normalize_running(float *z, size_t T, size_t stride, struct fe_norm_state *s) {
  for (size_t t = 0; t < T; t++) {
    s->count++;
    for (int b = 0; b < 64; b++) {
      float *x = z + (size_t)b * stride + t;
      double delta = (double)*x - s->mean[b];
      s->mean[b] += delta / (double)s->count;
      s->m2[b] += delta * ((double)*x - s->mean[b]);
      double std = sqrt(s->m2[b] / (double)(s->count - 1 > 1 ? s->count - 1 : 1));
      *x = (float)(((double)*x - s->mean[b]) / (std + 1e-5));
    }
  }
}

void fe_quantize(const float *f, size_t n, float scale, int8_t *out) {
  for (size_t i = 0; i < n; i++) {
    float x = f[i] / scale, v = copysignf(floorf(fabsf(x) + 0.5f), x);
    out[i] = v < -128 ? -128 : v > 127 ? 127 : (int8_t)v;
  }
}
