#ifndef QNRT_H
#define QNRT_H
#include <stddef.h>
#include <stdint.h>
typedef struct qnrt qnrt;
/* One context per concurrent caller. Calls on distinct contexts are
 * independent. max_frames bounds the allocated input length T of every
 * later call; threads is 1..4 (the calling thread is one of them). */
int qnrt_load(qnrt **ctx, const char *blob, unsigned max_frames,
              unsigned threads);
void qnrt_free(qnrt *ctx);
float qnrt_input_scale(const qnrt *ctx);
unsigned qnrt_layers(const qnrt *ctx);
/* Network on already-quantised INT8 features, channel-major [64][frames]
 * (band b, frame t at b*frames+t). Only the first `valid` frames are real;
 * the rest are masked exactly as the integer contract requires. Logits are
 * channel-major signed bytes [29][out_frames], owned by the context and valid
 * until the next call. Read only out_valid frames for CTC. */
int qnrt_run(qnrt *ctx, const int8_t *features, unsigned frames, unsigned valid,
             const int8_t **logits, unsigned *out_frames, unsigned *out_valid);
/* Same, with frame-major features [frames][64] (frame t, band b at t*64+b),
 * the order the streaming front end produces. Identical logits. */
int qnrt_run_frames(qnrt *ctx, const int8_t *features, unsigned frames,
                    unsigned valid, const int8_t **logits, unsigned *out_frames,
                    unsigned *out_valid);
/* Per-layer wall time of each later qnrt_run*: seconds[i] for descriptor i,
 * array of qnrt_layers() doubles owned by the caller. NULL disables. */
void qnrt_profile(qnrt *ctx, double *seconds);
/* Allocated output frames and convolution MACs for an input of `frames`
 * (pass the valid length to count only useful MACs). */
unsigned qnrt_output_frames(const qnrt *ctx, unsigned frames);
uint64_t qnrt_macs(const qnrt *ctx, unsigned frames);
enum { QNRT_POINTWISE, QNRT_DEPTHWISE, QNRT_CONV, QNRT_ADD };
int qnrt_layer_kind(const qnrt *ctx, unsigned layer);
/* One descriptor on caller channel-major buffers (test/debug path: it
 * transposes through context scratch). Signedness follows graph
 * descriptors; out must not alias inputs. */
int qnrt_layer(qnrt *ctx, unsigned layer, const void *a, const void *b,
               unsigned frames, unsigned valid, void *out, unsigned *out_frames,
               unsigned *out_valid);
#endif
