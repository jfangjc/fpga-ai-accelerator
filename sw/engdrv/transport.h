#ifndef ENG_TRANSPORT_H
#define ENG_TRANSPORT_H
#include "engdrv.h"
#ifdef __cplusplus
extern "C" {
#endif
struct transport;
int board_open(struct transport **, const struct engdrv_config *);
int cosim_open(struct transport **, const struct engdrv_config *);
struct transport {
  void *state;
  int (*exchange)(struct transport *, const void *, size_t, void *, size_t *,
                  struct engdrv_timing *);
  int (*reset)(struct transport *);
  uint32_t (*version)(struct transport *);
  /* Optional RUN_NETWORK support. program() stores an immutable QNP2 image in
   * DMA-visible memory once (ENOSPC if it does not fit). network() sends the
   * header+features packet head followed by that resident image as a single
   * AXIS packet and receives one response like exchange(). */
  int (*program)(struct transport *, const void *, size_t);
  int (*network)(struct transport *, const void *, size_t, void *, size_t *,
                 struct engdrv_timing *);
  /* Accumulated cache-maintenance time and call count (board only). */
  uint64_t sync_ns;
  unsigned sync_calls;
  void (*close)(struct transport *);
};
#ifdef __cplusplus
}
#endif
#endif
