/* Streaming front-end regression: for each WAV, chunked qnf_stream_* output
 * must equal whole-utterance qnf_features (running mode, same prior) bit for
 * bit at every frame, for several chunkings. With a blob, the quantised
 * frame-major stream must also give the same logits through qnrt_run_frames
 * as the channel-major features through qnrt_run.
 * usage: qnrt_stream_test prior.json [blob] -- a.wav b.wav ... */
#define _POSIX_C_SOURCE 200809L
#include "../frontend_c/frontend.h"
#include "qnrt.h"
#include "wav.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static unsigned lcg(unsigned *s) { return *s = *s * 1103515245u + 12345u; }
int main(int argc, char **argv) {
  int sep = 1;
  while (sep < argc && strcmp(argv[sep], "--"))
    sep++;
  if (sep < 2 || sep >= argc - 1) {
    fprintf(stderr, "usage: qnrt_stream_test prior.json [blob] -- wav...\n");
    return 2;
  }
  qnf_state prior;
  if (qnf_prior(&prior, argv[1]))
    return 1;
  int fails = 0;
  for (int a = sep + 1; a < argc; a++) {
    size_t n;
    float *x = qn_wav(argv[a], &n);
    if (!x || n <= 256)
      return 1;
    unsigned t = n / 160 + 1, T = (t + 15) / 16 * 16;
    float *whole = malloc((size_t)64 * T * sizeof(float)),
          *frames = malloc((size_t)64 * (t + 1) * sizeof(float));
    qnf_state st = prior;
    if (!whole || !frames || qnf_features(x, n, 1, &st, whole) != (int)T)
      return 1;
    /* chunkings: 1 sample, 10 ms, 160..4000 random, 37 random with max=1 */
    for (int mode = 0; mode < 4; mode++) {
      qnf_stream *s = qnf_stream_new(&prior);
      if (!s)
        return 1;
      unsigned seed = 7u + mode, got = 0, first_ready = 0;
      size_t pos = 0;
      while (pos < n) {
        size_t k = mode == 0   ? 1
                   : mode == 1 ? 160
                   : mode == 2 ? 160 + lcg(&seed) % 3841
                               : 1 + lcg(&seed) % 37;
        if (k > n - pos)
          k = n - pos;
        unsigned cap = mode == 3 ? 1 : t + 1 - got;
        int r = qnf_stream_push(s, x + pos, k, frames + (size_t)64 * got, cap);
        if (r < 0)
          return 1;
        pos += k;
        got += r;
        /* Each centered frame waits for 256 future samples. */
        if (got && (size_t)(got - 1) * 160 + 256 > pos)
          return fprintf(stderr, "FAIL early frame\n"), 1;
        if (!first_ready && got)
          first_ready = pos;
      }
      int r;
      while ((r = qnf_stream_flush(s, frames + (size_t)64 * got,
                                   mode == 3 ? 1 : t + 1 - got)) > 0)
        got += r;
      if (r < 0 || got != t)
        return fprintf(stderr, "FAIL frame count %u != %u\n", got, t), 1;
      if (memcmp(qnf_stream_state(s), &st, sizeof(st)))
        return fprintf(stderr, "FAIL final running statistics\n"), 1;
      unsigned bad = 0;
      for (unsigned j = 0; j < t; j++)
        for (int c = 0; c < 64; c++)
          bad += memcmp(&frames[(size_t)j * 64 + c], &whole[(size_t)c * T + j],
                        sizeof(float)) != 0;
      printf("%s %s chunking=%d frames=%u first_frame_at_sample=%u mismatches=%u\n",
             bad ? "FAIL" : "PASS", argv[a], mode, t, first_ready, bad);
      fails += bad != 0;
      qnf_stream_free(s);
    }
    if (sep > 2) {
      qnrt *c;
      if (qnrt_load(&c, argv[2], T, 4))
        return 1;
      int8_t *q = malloc((size_t)64 * T), *qf = calloc((size_t)64 * T, 1);
      qnf_quantize(whole, (size_t)64 * T, qnrt_input_scale(c), q);
      qnf_quantize(frames, (size_t)64 * t, qnrt_input_scale(c), qf);
      const int8_t *y;
      unsigned ot, ov, ot2, ov2;
      size_t bytes;
      if (qnrt_run(c, q, T, t, &y, &ot, &ov))
        return 1;
      bytes = (size_t)29 * ot;
      int8_t *ref = malloc(bytes);
      memcpy(ref, y, bytes);
      if (qnrt_run_frames(c, qf, T, t, &y, &ot2, &ov2))
        return 1;
      int same = ot == ot2 && ov == ov2 && !memcmp(ref, y, bytes);
      printf("%s %s run_frames(stream) == run(whole) logits\n",
             same ? "PASS" : "FAIL", argv[a]);
      fails += !same;
      free(ref);
      free(q);
      free(qf);
      qnrt_free(c);
    }
    free(x);
    free(whole);
    free(frames);
  }
  return fails != 0;
}
