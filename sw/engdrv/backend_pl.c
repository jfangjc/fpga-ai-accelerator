#include "../app/backend.h"
#include "engdrv.h"
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static int submit(struct cap_backend *b, const int8_t *x, unsigned t,
                  unsigned v) {
  return engdrv_submit(b->context, x, t, v);
}
static int poll_result(struct cap_backend *b, struct cap_result *r, int ms) {
  struct engdrv_timing t;
  struct engdrv_run_info info;
  int n = engdrv_poll(b->context, &r->logits, &r->stride, &r->valid, &t, ms);
  if (n == 1) {
    r->compute_ms = (t.ready_ns - t.submit_ns) / 1e6;
    r->engine = "pl-layer";
    r->commands = 0;
    if (!engdrv_last_run(b->context, &info)) {
      r->engine = info.network ? "pl-network" : "pl-layer";
      r->commands = info.commands;
    }
  }
  return n;
}
static void destroy(struct cap_backend *b) {
  engdrv_close(b->context);
  memset(b, 0, sizeof(*b));
}
/* Engine mode: ENGDRV_MODE=auto|layer|network (default auto: RUN_NETWORK when
 * the engine and buffers support it and the window fits, per-layer
 * otherwise, including finals longer than the network limit). */
int backend_pl(struct cap_backend *b, const char *path, float scale) {
  if (!path || !isfinite(scale) || scale <= 0) {
    errno = EINVAL;
    return -1;
  }
  const char *m = getenv("ENGDRV_MODE");
  int mode = ENGDRV_AUTO;
  if (m && !strcmp(m, "layer"))
    mode = ENGDRV_LAYER;
  else if (m && !strcmp(m, "network"))
    mode = ENGDRV_NETWORK;
  else if (m && *m && strcmp(m, "auto")) {
    errno = EINVAL;
    return -1;
  }
  engdrv *e;
  struct engdrv_config c = {0, 2, 3, 10000};
  if (engdrv_open(&e, &c))
    return -1;
  if (engdrv_load(e, path) || engdrv_set_mode(e, mode)) {
    int err = errno;
    engdrv_close(e);
    errno = err;
    return -1;
  }
  uint32_t version = 0;
  engdrv_version(e, &version);
  fprintf(stderr, "backend pl: engine 0x%08x, RUN_NETWORK %s, mode %s\n",
          version, engdrv_network_capable(e) ? "available" : "unavailable",
          mode == ENGDRV_LAYER ? "layer" : mode == ENGDRV_NETWORK ? "network"
                                                                  : "auto");
  memset(b, 0, sizeof(*b));
  b->context = e;
  b->submit = submit;
  b->poll = poll_result;
  b->destroy = destroy;
  b->scale = scale;
  return 0;
}
