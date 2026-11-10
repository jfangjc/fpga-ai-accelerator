#ifndef QN_FRONTEND_H
#define QN_FRONTEND_H
#include <stddef.h>
#include <stdint.h>
typedef struct {
  double count, mean[64], m2[64];
} qnf_state;
/* Whole utterance. Caller allocates 64 * ceil((samples/160+1)/16)*16 values,
 * channel-major. 16 kHz mono. Returns the padded frame count or -1. */
int qnf_features(const float *audio, size_t samples, int running,
                 qnf_state *state, float *out);
/* Same result without heap allocation: scratch holds at least
 * qnf_features_scratch(samples) bytes, 64-byte aligned, and is not shared
 * between concurrent calls. */
size_t qnf_features_scratch(size_t samples);
int qnf_features_ws(const float *audio, size_t samples, int running,
                    qnf_state *state, float *out, void *scratch);
void qnf_quantize(const float *features, size_t count, float scale,
                  int8_t *out);
int qnf_prior(qnf_state *state, const char *json);
/* Streaming front end with running normalisation. Push PCM chunks of any
 * size; frame j (frame-major, 64 floats at out + 64*k) is emitted as soon as
 * 160j+256 samples have been pushed, i.e. 16 ms after its centre. Preemphasis
 * and window history persist across chunks, so frames equal qnf_features
 * (running mode, same prior) exactly; the first frames use the utterance-start
 * reflection. At most `max` frames are written per call; undelivered ready
 * frames are returned by later push (n may be 0) or flush calls. flush marks
 * the utterance end and emits the remaining frames (total samples/160+1)
 * with end reflection; it needs more than 256 samples in total.
 * prior NULL starts statistics at count 0. */
typedef struct qnf_stream qnf_stream;
qnf_stream *qnf_stream_new(const qnf_state *prior);
void qnf_stream_reset(qnf_stream *s, const qnf_state *prior);
void qnf_stream_free(qnf_stream *s);
int qnf_stream_push(qnf_stream *s, const float *pcm, size_t samples,
                    float *out, unsigned max);
int qnf_stream_flush(qnf_stream *s, float *out, unsigned max);
const qnf_state *qnf_stream_state(const qnf_stream *s);
#endif
