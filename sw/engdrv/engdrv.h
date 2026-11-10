#ifndef ENGDRV_H
#define ENGDRV_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct engdrv engdrv;
enum engdrv_status {
  ENGDRV_OK,
  ENGDRV_BAD_VERSION,
  ENGDRV_BAD_OPCODE,
  ENGDRV_BAD_LENGTH,
  ENGDRV_BAD_DESCRIPTOR,
  ENGDRV_OVERFLOW,
  ENGDRV_IDLE_TIMEOUT,
  ENGDRV_INTERNAL
};
const char *engdrv_status_name(uint32_t status);
struct engdrv_timing {
  uint64_t submit_ns, irq_ns, ready_ns, cycles;
  uint32_t status, starvation, terms;
};
struct engdrv_config {
  int cosim;
  unsigned tx_buffer, rx_buffer;
  int timeout_ms;
};
int engdrv_open(engdrv **, const struct engdrv_config *);
void engdrv_close(engdrv *);
int engdrv_version(engdrv *, uint32_t *);
int engdrv_ping(engdrv *, struct engdrv_timing *);
int engdrv_load(engdrv *, const char *qnp2);
/* Raw packets are useful for protocol diagnostics. Capacity includes QNS2
 * header. */
int engdrv_exchange(engdrv *, const void *, size_t, void *, size_t *,
                    struct engdrv_timing *);
int engdrv_recover(engdrv *);
unsigned engdrv_layers(const engdrv *);
int engdrv_run_layer(engdrv *, unsigned layer, const void *a, const void *b,
                     unsigned frames, unsigned valid, int input_signed,
                     void *out, size_t capacity, unsigned *out_frames,
                     unsigned *out_valid, struct engdrv_timing *);
int engdrv_run_network(engdrv *, const int8_t *, unsigned, unsigned,
                       const int8_t **, unsigned *, unsigned *,
                       struct engdrv_timing *);
/* Per-command timing recorded by the most recent successful network run:
 * one entry per layer in per-layer mode (tiled layers sum their commands),
 * a single entry (index 0) in RUN_NETWORK mode. */
int engdrv_network_timing(const engdrv *, unsigned layer,
                          struct engdrv_timing *);
/* Network execution mode. AUTO sends one RUN_NETWORK command when the engine
 * reports version >= 0x00010002, the transport holds the resident program and
 * the window fits the network limit (1..640 allocated input frames); otherwise
 * it runs the per-layer RUN_LAYER graph. LAYER forces per-layer mode; NETWORK
 * fails with ENOTSUP/E2BIG instead of falling back. */
enum engdrv_mode { ENGDRV_AUTO, ENGDRV_LAYER, ENGDRV_NETWORK };
int engdrv_set_mode(engdrv *, int mode);
/* 1 if RUN_NETWORK is usable with the loaded program, 0 otherwise. */
int engdrv_network_capable(const engdrv *);
#define ENGDRV_NETWORK_FRAMES 640u
/* Per-layer mode time-tiles commands longer than the 800-frame RUN_LAYER
 * limit, so whole networks accept up to this many input frames. */
#define ENGDRV_MAX_FRAMES 2048u
/* Most recent successful network run. exchange_ns sums every command's
 * transport time (TX copy, cache maintenance, DMA, engine, IRQ wait, RX copy);
 * outside_ns = wall_ns - exchange_ns is graph walking, encoding, tiling and
 * logits copies. sync_ns/sync_calls count range cache maintenance (board). */
struct engdrv_run_info {
  int network; /* 1 RUN_NETWORK, 0 per-layer */
  unsigned commands;
  uint64_t wall_ns, exchange_ns, outside_ns, sync_ns;
  unsigned sync_calls;
};
int engdrv_last_run(const engdrv *, struct engdrv_run_info *);
/* One outstanding request; submit copies features. poll: 1 ready, 0 pending, -1
 * error. */
int engdrv_submit(engdrv *, const int8_t *, unsigned, unsigned);
int engdrv_poll(engdrv *, const int8_t **, unsigned *, unsigned *,
                struct engdrv_timing *, int timeout_ms);
#ifdef __cplusplus
}
#endif
#endif
