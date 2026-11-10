#ifndef CAP_BACKEND_H
#define CAP_BACKEND_H
#include <stdint.h>
#include "../qnrt/qnrt.h"
/* Requests are channel-major [64][frames], padded to 16; valid <= frames.
 * Logits are [29][stride]. Lifetime ends at next submit or destruction.
 * submit/poll permit a future asynchronous PL implementation. */
struct cap_result {
    const int8_t *logits;
    unsigned stride, valid;
    double compute_ms;
    /* "cpu", "pl-network" (one RUN_NETWORK) or "pl-layer" (RUN_LAYER graph);
     * commands is the engine command count (0 for CPU). */
    const char *engine;
    unsigned commands;
};
struct cap_backend {
    void *context;
    float scale;
    int (*submit)(struct cap_backend *, const int8_t *, unsigned, unsigned);
    int (*poll)(struct cap_backend *, struct cap_result *, int timeout_ms);
    void (*destroy)(struct cap_backend *);
    struct cap_result result;
};
int backend_cpu(struct cap_backend *, const char *, unsigned, unsigned);
/* PL engine (sw/engdrv) on a QNP2 program; builds with HAVE_ENGDRV only.
 * Otherwise fails with ENOSYS. scale is the input quantisation scale (the
 * matching QNW1 blob's), so PL and CPU quantise identically. */
int backend_pl(struct cap_backend *, const char *program, float scale);
#endif
