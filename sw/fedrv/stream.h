#ifndef FE_STREAM_H
#define FE_STREAM_H
/* Live-app streaming on top of fedrv: an IRQ-driven capture thread pushes
 * frames into a lock-free single-producer/single-consumer ring; an optional
 * injection thread streams samples from a callback (e.g. a looping WAV)
 * through the MM2S descriptor ring. The consumer never touches MMIO or DMA
 * memory. A full consumer ring drops the newest frame and counts it. */
#include "fedrv.h"
#include <pthread.h>
#include <semaphore.h>

#define FE_STREAM_FRAMES 4096u /* power of two; 41 s at 100 frames/s */

/* Fill up to max signed-18 samples; return count, 0 at end of input. */
typedef size_t (*fe_fill_fn)(void *ctx, int32_t *dst, size_t max);

struct fe_stream {
  struct fe *device;
  pthread_t capture, inject;
  int has_inject;
  fe_fill_fn fill;
  void *fill_ctx;
  sem_t ready;
  _Atomic uint32_t head, tail; /* head: written by capture; tail: by consumer */
  _Atomic int stop, inject_done, capture_done, error;
  _Atomic uint64_t dropped;      /* consumer ring full */
  uint64_t expected;             /* frames to capture once injection ends */
  struct fe_frame ring[FE_STREAM_FRAMES];
};

/* fe must be reset and configured. With fill != NULL (injection source) the
 * stream ends after the frames produced by all injected samples; otherwise
 * (microphones) it runs until fe_stream_stop. Starts the device. */
int fe_stream_start(struct fe_stream *, struct fe *, fe_fill_fn fill, void *ctx);
/* 1 frame, 0 end of stream, -1 error (errno), -2 timeout. */
int fe_stream_next(struct fe_stream *, struct fe_frame *, int timeout_ms);
void fe_stream_stop(struct fe_stream *); /* request stop; then join */
int fe_stream_join(struct fe_stream *);  /* returns the thread error or 0 */
#endif
