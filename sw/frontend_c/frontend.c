#include "frontend.h"
#include <math.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define PI 3.14159265358979323846
/* Constant tables. Every value is produced by the same expression the
 * original per-call code used, so features are bit-identical: twiddles are
 * cos/sin evaluated in double then rounded to float, and mel sums skip only
 * filter weights that are exactly zero (adding +0 to a non-negative float
 * sum is the identity). */
typedef struct {
  float fb[64][257], win[320], twr[511], twi[511];
  int lo[64], hi[64];
} tables;
static double mel(double f) {
  return f < 1000 ? f / (200.0 / 3) : 15 + log(f / 1000) / (log(6.4) / 27);
}
static double hz(double m) {
  return m < 15 ? m * (200.0 / 3) : 1000 * exp((m - 15) * (log(6.4) / 27));
}
static void init_tables(tables *tb) {
  double edge[66];
  for (int j = 0; j < 66; j++)
    edge[j] = hz(mel(8000) * j / 65);
  for (int j = 0; j < 320; j++)
    tb->win[j] = .5f - .5f * cosf((float)(2 * PI) * j / 319);
  for (int c = 0; c < 64; c++) {
    tb->lo[c] = 257;
    tb->hi[c] = 0;
    for (int k = 0; k < 257; k++) {
      double f = k * 16000.0 / 512;
      double w = fmin((f - edge[c]) / (edge[c + 1] - edge[c]),
                      (edge[c + 2] - f) / (edge[c + 2] - edge[c + 1]));
      tb->fb[c][k] = (float)fmax(0, w);
      tb->fb[c][k] *= (float)(2 / (edge[c + 2] - edge[c]));
      if (tb->fb[c][k] != 0) {
        if (k < tb->lo[c])
          tb->lo[c] = k;
        tb->hi[c] = k + 1;
      }
    }
  }
  /* stage n uses entries [n/2-1, n-1) */
  for (unsigned n = 2; n <= 512; n *= 2)
    for (unsigned j = 0; j < n / 2; j++) {
      tb->twr[n / 2 - 1 + j] = cos(2 * PI * j / n);
      tb->twi[n / 2 - 1 + j] = -sin(2 * PI * j / n);
    }
}
static void fft(const tables *tb, float *r, float *i) {
  for (unsigned j = 0, k = 0; j < 512; j++) {
    if (j < k) {
      float z = r[j];
      r[j] = r[k];
      r[k] = z;
    }
    unsigned b = 256;
    for (; k & b; b >>= 1)
      k ^= b;
    k ^= b;
  }
  for (unsigned n = 2; n <= 512; n *= 2) {
    const float *cr = tb->twr + n / 2 - 1, *ci = tb->twi + n / 2 - 1;
    for (unsigned p = 0; p < 512; p += n)
      for (unsigned j = 0; j < n / 2; j++) {
        float wr = cr[j], wi = ci[j];
        unsigned a = p + j, b = a + n / 2;
        float tr = wr * r[b] - wi * i[b], ti = wr * i[b] + wi * r[b];
        r[b] = r[a] - tr;
        i[b] = i[a] - ti;
        r[a] += tr;
        i[a] += ti;
      }
  }
}
/* Log-mel of frame j. y holds preemphasised samples from absolute index base;
 * reflection at the start always, at the end only when n (total samples) is
 * known (n == 0 means the end is not yet known and is never reached). */
static void logmel(const tables *tb, const float *y, long base, long n, long j,
                   float *out, size_t ostride) {
  float r[512] = {0}, im[512] = {0};
  for (int k = 0; k < 320; k++) {
    long p = j * 160 + k - 160;
    if (p < 0)
      p = -p;
    if (n && p >= n)
      p = 2 * n - 2 - p;
    r[k + 96] = y[p - base] * tb->win[k];
  }
  fft(tb, r, im);
  float pow[257];
  for (int k = 0; k < 257; k++) {
    float a = hypotf(r[k], im[k]);
    pow[k] = a * a;
  }
  for (int c = 0; c < 64; c++) {
    float v = 0;
    for (int k = tb->lo[c]; k < tb->hi[c]; k++)
      v += tb->fb[c][k] * pow[k];
    out[(size_t)c * ostride] = logf(v + 0x1p-24f);
  }
}
/* Causal Welford step including the current frame. */
static void running(qnf_state *s, const float *z, size_t zstride, float *out,
                    size_t ostride) {
  s->count++;
  for (int c = 0; c < 64; c++) {
    double a = z[(size_t)c * zstride], d = a - s->mean[c];
    s->mean[c] += d / s->count;
    s->m2[c] += d * (a - s->mean[c]);
    out[(size_t)c * ostride] =
        (a - s->mean[c]) / (sqrt(s->m2[c] / fmax(1, s->count - 1)) + 1e-5);
  }
}
/* Scratch layout: tables, preemphasised audio, then log-mel frames. Each
 * part starts on a 64-byte boundary. */
#define QNF_ALIGN(v) (((v) + 63) & ~(size_t)63)
size_t qnf_features_scratch(size_t n) {
  return QNF_ALIGN(sizeof(tables)) + QNF_ALIGN(n * sizeof(float)) +
         (size_t)64 * (n / 160 + 1) * sizeof(float);
}
int qnf_features(const float *x, size_t n, int run, qnf_state *s, float *out) {
  if (n <= 256)
    return -1;
  void *scratch = malloc(qnf_features_scratch(n));
  if (!scratch)
    return -1;
  int T = qnf_features_ws(x, n, run, s, out, scratch);
  free(scratch);
  return T;
}
int qnf_features_ws(const float *x, size_t n, int run, qnf_state *s,
                    float *out, void *scratch) {
  if (n <= 256 || !x || !out || !scratch || (run && !s))
    return -1;
  unsigned t = n / 160 + 1, T = (t + 15) / 16 * 16;
  unsigned char *base = scratch;
  tables *tb = (tables *)base;
  float *y = (float *)(base + QNF_ALIGN(sizeof(tables))),
        *z = (float *)(base + QNF_ALIGN(sizeof(tables)) +
                       QNF_ALIGN(n * sizeof(float)));
  for (size_t j = 0; j < n; j++) {
    if (!isfinite(x[j]))
      return -1;
    y[j] = j ? x[j] - .97f * x[j - 1] : x[j];
  }
  init_tables(tb);
  for (unsigned j = 0; j < t; j++)
    logmel(tb, y, 0, (long)n, j, z + j, t);
  memset(out, 0, (size_t)64 * T * sizeof(float));
  if (run) {
    for (unsigned j = 0; j < t; j++)
      running(s, z + j, t, out + j, T);
  } else
    for (int c = 0; c < 64; c++) {
      float mean = 0, var = 0;
      for (unsigned j = 0; j < t; j++)
        mean += z[(size_t)c * t + j];
      mean /= t;
      for (unsigned j = 0; j < t; j++) {
        float d = z[(size_t)c * t + j] - mean;
        var += d * d;
      }
      float std = sqrtf(var / (t - 1)) + 1e-5f;
      for (unsigned j = 0; j < t; j++)
        out[(size_t)c * T + j] = (z[(size_t)c * t + j] - mean) / std;
    }
  return T;
}
void qnf_quantize(const float *f, size_t n, float s, int8_t *out) {
  for (size_t j = 0; j < n; j++) {
    float x = f[j] / s;
    float v = copysignf(floorf(fabsf(x) + .5f), x);
    out[j] = v < -128 ? -128 : v > 127 ? 127 : (int8_t)v;
  }
}
int qnf_prior(qnf_state *s, const char *path) {
  FILE *f = fopen(path, "r");
  if (!f)
    return -1;
  char b[16384];
  size_t n = fread(b, 1, sizeof(b) - 1, f);
  fclose(f);
  b[n] = 0;
  const char *keys[] = {"\"count\"", "\"mean\"", "\"m2\""};
  for (int k = 0; k < 3; k++) {
    char *p = strstr(b, keys[k]);
    if (!p)
      return -1;
    p = strchr(p, ':');
    if (!p)
      return -1;
    p++;
    if (k) {
      p = strchr(p, '[');
      if (!p)
        return -1;
      p++;
    }
    for (int j = 0; j < (k ? 64 : 1); j++) {
      while (*p == ' ' || *p == '\n' || *p == '\r' || *p == ',')
        p++;
      char *end;
      double v = strtod(p, &end);
      if (end == p || !isfinite(v))
        return -1;
      if (!k)
        s->count = v;
      else if (k == 1)
        s->mean[j] = v;
      else
        s->m2[j] = v;
      p = end;
    }
  }
  return s->count >= 0 ? 0 : -1;
}
/* Streaming: y keeps preemphasised samples [base, base+len). Frame j needs
 * samples 160j-160 .. 160j+159 (the 320-sample Hann window centred in the
 * 512-point frame), plus up to 160j-479 for end-of-utterance reflection. */
struct qnf_stream {
  tables tb;
  qnf_state st;
  float *y, last;
  size_t cap, len, base, total;
  long next;
  int ended;
};
qnf_stream *qnf_stream_new(const qnf_state *prior) {
  qnf_stream *s = calloc(1, sizeof(*s));
  if (!s)
    return NULL;
  init_tables(&s->tb);
  qnf_stream_reset(s, prior);
  return s;
}
void qnf_stream_reset(qnf_stream *s, const qnf_state *prior) {
  if (prior)
    s->st = *prior;
  else
    memset(&s->st, 0, sizeof(s->st));
  s->len = s->base = s->total = 0;
  s->next = 0;
  s->ended = 0;
  s->last = 0;
}
void qnf_stream_free(qnf_stream *s) {
  if (s)
    free(s->y);
  free(s);
}
const qnf_state *qnf_stream_state(const qnf_stream *s) { return &s->st; }
static int emit(qnf_stream *s, long end, long n, float *out, unsigned max) {
  unsigned k = 0;
  for (; s->next < end && k < max; s->next++, k++) {
    float z[64];
    logmel(&s->tb, s->y, (long)s->base, n, s->next, z, 1);
    running(&s->st, z, 1, out + (size_t)k * 64, 1);
  }
  /* drop samples no later frame can reach */
  long keep = s->next * 160 - 480;
  if (keep > (long)s->base) {
    size_t drop = (size_t)keep - s->base;
    if (drop > s->len)
      drop = s->len;
    memmove(s->y, s->y + drop, (s->len - drop) * sizeof(float));
    s->len -= drop;
    s->base += drop;
  }
  return (int)k;
}
int qnf_stream_push(qnf_stream *s, const float *x, size_t n, float *out,
                    unsigned max) {
  if (!s || s->ended || (n && !x) || (max && !out))
    return -1;
  /* Reject before changing history, including partial invalid chunks. */
  if (n > (size_t)LONG_MAX - s->total ||
      n > SIZE_MAX / sizeof(float) / 2 - 1024 - s->len)
    return -1;
  for (size_t j = 0; j < n; j++)
    if (!isfinite(x[j]))
      return -1;
  if (s->len + n > s->cap) {
    size_t cap = (s->len + n) * 2 + 1024;
    float *y = realloc(s->y, cap * sizeof(float));
    if (!y)
      return -1;
    s->y = y;
    s->cap = cap;
  }
  for (size_t j = 0; j < n; j++) {
    s->y[s->len++] = s->total + j ? x[j] - .97f * (j ? x[j - 1] : s->last)
                                  : x[j];
  }
  if (n)
    s->last = x[n - 1];
  s->total += n;
  /* Preserve the centered 512-point frame's 256 future-sample contract,
   * including start reflection (which reads index 160 for frame zero). */
  long ready = s->total >= 256 ? (long)((s->total - 256) / 160) + 1 : 0;
  return emit(s, ready, 0, out, max);
}
int qnf_stream_flush(qnf_stream *s, float *out, unsigned max) {
  if (!s || s->total <= 256 || (max && !out))
    return -1;
  s->ended = 1;
  return emit(s, (long)(s->total / 160) + 1, (long)s->total, out, max);
}
