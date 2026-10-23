#ifndef QNC_H
#define QNC_H
#include <stddef.h>
#include <stdint.h>
/* One context per inference worker. Not thread safe. Return 0 or negative errno. */
typedef struct qnc qnc;
typedef int (*qnc_exchange)(void *, const uint8_t *, uint8_t *, uint32_t, uint32_t, uint32_t, int);
typedef struct { uint64_t ioctls, loads, runs, tx_bytes, rx_bytes; double ioctl_seconds; } qnc_stats;
/* Separate getter preserves original qnc_stats ABI and Python callers. Times
 * are subsets of the pointwise call, not extra costs to add to total time. */
typedef struct { double load_pack_seconds, scan_seconds, run_pack_seconds, unpack_seconds;
 uint64_t mac_issue_cycles,input_stall_cycles,output_stall_cycles,active_cycles; } qnc_profile;
qnc *qnc_open(const char *path);
/* Callback transport is for protocol simulation; never reports hardware execution. */
qnc *qnc_create(qnc_exchange exchange, void *opaque);
void qnc_close(qnc *ctx);
int qnc_select_slot(qnc *ctx, unsigned slot);
int qnc_load(qnc *ctx, const int8_t *weights, unsigned m, unsigned k);
/* Raw signed weights on the single-cache BULK image (opcode 1).
 * Not compatible with the retained branch image. Invalidates other residents. */
int qnc_load_i8_single(qnc *ctx, const int8_t *weights, unsigned m, unsigned k);
/* Mutable 16-byte header plus prepacked [ceil(M/16),K,16] signed weights.
 * Header is overwritten on each call. Packet must remain alive through return.
 * Single-cache bulk image only; retains the original raw load API above. */
int qnc_load_i8_single_packet(qnc *ctx, uint8_t *packet, size_t bytes, unsigned m, unsigned k);
int qnc_load_quant(qnc *ctx, const int8_t *weights, unsigned m, unsigned k,
                   const int32_t *bias, const int32_t *multiplier,
                   const uint8_t *shift, uint8_t zero);
int qnc_run_quant(qnc *ctx, const int8_t *x, size_t times, uint8_t *y);
int qnc_run_bulk(qnc *ctx, const uint8_t *x, size_t times, uint8_t *y);
int qnc_load_depthwise(qnc *ctx, const int8_t *weights, const int32_t *bias,
                      const int32_t *multiplier, const uint8_t *shift,
                      uint8_t xzero, uint8_t yzero, unsigned kernel);
int qnc_run_pair(qnc *ctx, const uint8_t *x, size_t times, size_t valid_times, uint8_t *y);
typedef struct { unsigned slot; uint8_t masked_value; const uint8_t *lookup; } qnc_chain_stage;
int qnc_run_chain(qnc *ctx,const qnc_chain_stage *stages,unsigned count,
                  const uint8_t *x,size_t times,size_t valid_times,uint8_t *y);
int qnc_run_stream(qnc *ctx,const uint8_t *prefix,size_t prefix_bytes,unsigned count,unsigned tag,
                  const uint8_t *x,size_t times,size_t valid_times,uint8_t *y);
int qnc_run_stream_reuse(qnc *ctx,uint8_t *packet,size_t prefix_bytes,size_t capacity,unsigned count,unsigned tag,
                        const uint8_t *x,size_t times,size_t valid_times,uint8_t *y);
int qnc_register_stream(qnc *ctx,const uint8_t *prefix,size_t prefix_bytes,uint64_t *handle);
int qnc_release_stream(qnc *ctx,uint64_t handle);
int qnc_run_registered(qnc *ctx,uint64_t handle,uint8_t *packet,size_t prefix_bytes,size_t capacity,unsigned count,unsigned tag,
                       const uint8_t *x,size_t times,size_t valid_times,uint8_t *y);
int qnc_invalidate(qnc *ctx);
int qnc_run_i8(qnc *ctx, const int8_t *x, size_t times, int32_t *y);
/* Add raw FPGA results into an existing [M,T] INT32 buffer during unpacking.
 * Arithmetic wraps modulo 2^32; callers requiring exact sums must bound K. */
int qnc_run_i8_accumulate(qnc *ctx, const int8_t *x, size_t times, int32_t *y);
/* X[K,T], Y[M,T], contiguous. Whole-layer dynamic symmetric quantization,
 * nearest-even division rounding, saturation [-127,127]. No bias or activation. */
int qnc_run_f32(qnc *ctx, const float *x, size_t times, const float *weight_scales,
                float *y, float *activation_scale);
void qnc_get_stats(const qnc *ctx, qnc_stats *stats);
void qnc_get_profile(const qnc *ctx, qnc_profile *profile);
/* Host helpers: time-major float input -> channel-major INT8; channel-major
 * INT32 accumulation -> time-major float output. Same whole-matrix scale and
 * nearest-even rounding as the NumPy projection reference. No device access. */
int qnc_quantize_rows_f32(const float *x,size_t rows,unsigned k,int8_t *q,float *scale);
int qnc_restore_rows_i32(const int32_t *x,size_t rows,unsigned m,const float *scales,
                         float activation_scale,const float *bias,float *y);
int qnc_run_branch_reuse(qnc*,uint8_t*,size_t,size_t,unsigned,unsigned,const uint8_t*,size_t,size_t,uint8_t*,uint8_t*,unsigned);
int qnc_run_branch_registered(qnc*,uint64_t,uint8_t*,size_t,size_t,unsigned,unsigned,const uint8_t*,size_t,size_t,uint8_t*,uint8_t*,unsigned);
#endif
