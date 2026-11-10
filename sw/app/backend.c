#define _POSIX_C_SOURCE 200809L
#include "backend.h"
#include <errno.h>
#include <time.h>
static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}
static int submit(struct cap_backend *b, const int8_t *x, unsigned t, unsigned n) {
    double start = now();
    int rc = qnrt_run(b->context, x, t, n, &b->result.logits,
                      &b->result.stride, &b->result.valid);
    b->result.compute_ms = (now() - start) * 1000;
    b->result.engine = "cpu";
    return rc;
}
static int poll_result(struct cap_backend *b, struct cap_result *r, int timeout) {
    (void)timeout;
    *r = b->result;
    return 1;
}
static void destroy(struct cap_backend *b) { qnrt_free(b->context); }
int backend_cpu(struct cap_backend *b, const char *blob, unsigned max, unsigned threads) {
    qnrt *q;
    if (qnrt_load(&q, blob, max, threads)) return -1;
    *b = (struct cap_backend){.context=q, .scale=qnrt_input_scale(q),
          .submit=submit, .poll=poll_result, .destroy=destroy};
    return 0;
}
#ifndef HAVE_ENGDRV
/* Without HAVE_ENGDRV the PL backend is absent; never silently select CPU. */
int backend_pl(struct cap_backend *b, const char *program, float scale) {
    (void)b; (void)program; (void)scale;
    errno = ENOSYS;
    return -1;
}
#endif
