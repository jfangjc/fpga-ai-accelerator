#ifndef FE_FEATURES_H
#define FE_FEATURES_H
/* PS log + normalisation of PL mel power, ported operation-for-operation from
 * model/frontend.py:log_normalize (NumPy 2 float32/float64 semantics, including
 * NumPy's float32 pairwise summation), plus the model's INT8 input quantiser
 * (float32 divide, half-away rounding, saturate). Layout: band-major [64][T]. */
#include <stddef.h>
#include <stdint.h>

struct fe_norm_state { /* running Welford statistics, as in the model */
  long count;
  double mean[64], m2[64];
};

/* log(float32(power * 2^-42) + 2^-24) for one frame. */
void fe_log_frame(const uint64_t power[64], float z[64]);
/* Whole-utterance per-band mean / sample std (ddof=1); T >= 2. In place. */
int fe_normalize_utterance(float *z, size_t T);
/* Causal running statistics; state carries across calls (prior allowed). */
void fe_normalize_running(float *z, size_t T, size_t stride, struct fe_norm_state *);
void fe_quantize(const float *f, size_t n, float scale, int8_t *out);
#endif
