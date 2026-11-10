#define _POSIX_C_SOURCE 200809L
#include "stream.h"
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static void fail(struct fe_stream *s, int e) {
  int zero = 0;
  atomic_compare_exchange_strong(&s->error, &zero, e ? e : EIO);
  atomic_store(&s->stop, 1);
}

static void *inject_main(void *arg) {
  struct fe_stream *s = arg;
  int32_t *chunk = malloc(FE_TX_CHUNK * sizeof(*chunk));
  if (!chunk) fail(s, ENOMEM);
  while (chunk && !atomic_load(&s->stop)) {
    size_t n = s->fill(s->fill_ctx, chunk, FE_TX_CHUNK);
    if (!n) break;
    /* Long timeout: paced mode legitimately waits ~32 s for a free chunk. */
    if (fe_inject(s->device, chunk, n, 60000) < 0) {
      fail(s, errno);
      break;
    }
  }
  free(chunk);
  atomic_store(&s->inject_done, 1);
  return NULL;
}

static void *capture_main(void *arg) {
  struct fe_stream *s = arg;
  struct fe *f = s->device;
  struct fe_frame batch[64];
  uint64_t got = 0;
  while (!atomic_load(&s->stop)) {
    if (s->has_inject && atomic_load(&s->inject_done)) {
      uint64_t want = fe_frames_for(atomic_load(&f->tx_submitted));
      if (got >= want) break;
    }
    int n = fe_capture(f, batch, 64, 100);
    if (n < 0) {
      fail(s, errno);
      break;
    }
    for (int i = 0; i < n; i++) {
      uint32_t h = atomic_load_explicit(&s->head, memory_order_relaxed);
      if (h - atomic_load_explicit(&s->tail, memory_order_acquire) >= FE_STREAM_FRAMES) {
        atomic_fetch_add(&s->dropped, 1);
        continue;
      }
      s->ring[h % FE_STREAM_FRAMES] = batch[i];
      atomic_store_explicit(&s->head, h + 1, memory_order_release);
      sem_post(&s->ready);
    }
    got += (uint64_t)n;
  }
  atomic_store(&s->capture_done, 1);
  sem_post(&s->ready);
  return NULL;
}

int fe_stream_start(struct fe_stream *s, struct fe *f, fe_fill_fn fill, void *ctx) {
  memset(s, 0, sizeof(*s));
  s->device = f;
  s->fill = fill;
  s->fill_ctx = ctx;
  s->has_inject = fill != NULL;
  if (sem_init(&s->ready, 0, 0)) return -1;
  if (fe_start(f)) goto fail;
  int e = pthread_create(&s->capture, NULL, capture_main, s);
  if (e) {
    errno = e;
    goto fail;
  }
  if (fill && (e = pthread_create(&s->inject, NULL, inject_main, s))) {
    atomic_store(&s->stop, 1);
    pthread_join(s->capture, NULL);
    errno = e;
    goto fail;
  }
  return 0;
fail:
  e = errno;
  fe_stop(f);
  sem_destroy(&s->ready);
  errno = e;
  return -1;
}

int fe_stream_next(struct fe_stream *s, struct fe_frame *out, int ms) {
  for (;;) {
    uint32_t t = atomic_load_explicit(&s->tail, memory_order_relaxed);
    if (atomic_load_explicit(&s->head, memory_order_acquire) != t) {
      *out = s->ring[t % FE_STREAM_FRAMES];
      atomic_store_explicit(&s->tail, t + 1, memory_order_release);
      sem_trywait(&s->ready); /* keep the count roughly in step */
      return 1;
    }
    if (atomic_load(&s->capture_done)) {
      if (atomic_load(&s->head) != t) continue;
      int e = atomic_load(&s->error);
      if (e) {
        errno = e;
        return -1;
      }
      return 0;
    }
    struct timespec d;
    clock_gettime(CLOCK_REALTIME, &d);
    d.tv_sec += ms / 1000;
    d.tv_nsec += (long)(ms % 1000) * 1000000;
    if (d.tv_nsec >= 1000000000) {
      d.tv_sec++;
      d.tv_nsec -= 1000000000;
    }
    if (sem_timedwait(&s->ready, &d) && errno == ETIMEDOUT &&
        atomic_load_explicit(&s->head, memory_order_acquire) == t && !atomic_load(&s->capture_done))
      return -2;
  }
}

void fe_stream_stop(struct fe_stream *s) { atomic_store(&s->stop, 1); }

int fe_stream_join(struct fe_stream *s) {
  if (s->has_inject) pthread_join(s->inject, NULL);
  pthread_join(s->capture, NULL);
  fe_stop(s->device);
  sem_destroy(&s->ready);
  return atomic_load(&s->error);
}
