#ifndef FEDRV_H
#define FEDRV_H
/* Driver for the stt-fe design: stt_frontend (UIO stt-frontend) plus its
 * scatter-gather AXI DMA (UIO stt-fe-tx / stt-fe-rx, one register aperture)
 * and the GPIO front-end reset (UIO stt-fe-reset). See docs/platform.md.
 *
 * DMA buffers: udmabuf0 holds the MM2S descriptor ring and sample chunks,
 * udmabuf1 the cyclic S2MM descriptor ring and frame slots. Only the bytes
 * actually touched are cache-maintained (plio_buffer_sync_range).
 *
 * Threads: at most one injection thread (fe_inject*) and one capture thread
 * (fe_capture) may run concurrently; they use disjoint registers and memory.
 * Everything else (open/reset/configure/start/stop/LED/mic) belongs to the
 * owner thread and must not run concurrently with injection or capture. */
#include "../plio/plio.h"
#include <stdatomic.h>
#include <stddef.h>
#include <stdint.h>

#define FE_BANDS 64
#define FE_PACKET_BYTES 520
#define FE_EXPONENT (-42)
#define FE_RX_SLOTS 1024u    /* cyclic frame ring: 10.24 s at 100 frames/s */
#define FE_TX_SLOTS 128u     /* MM2S descriptor ring */
#define FE_TX_CHUNK 4096u    /* samples per MM2S descriptor (256 ms paced) */
#define FE_SAMPLE_CLOCKS 6250u /* paced spacing, 100 MHz clocks */

enum fe_source { FE_SRC_MIC1 = 0, FE_SRC_MIC2 = 1, FE_SRC_BEAM = 2, FE_SRC_INJECT = 3 };

struct fe_config {
  enum fe_source source;
  int paced;            /* injection: 1 = 16 kHz real time, 0 = bulk */
  unsigned irq_frames;  /* capture IRQ every N frames, 1..255 */
  int right_channel;    /* I2S slot matching the microphone SEL pin */
  int button_irq;       /* include button presses in the stt-frontend IRQ */
  uint8_t delay1, delay2; /* beamformer integer delays (samples) */
  int16_t gain;         /* beamformer signed Q15 gain */
};

struct fe_frame {
  uint32_t sequence;
  uint64_t power[FE_BANDS]; /* band power = power * 2^-42 */
  uint64_t irq_ns;          /* capture thread woke for this frame's batch */
  uint64_t avail_ns;        /* frame copied out of DMA memory */
};

struct fe_stats {
  uint64_t frames;        /* frames delivered by fe_capture */
  uint64_t irqs;          /* S2MM interrupts serviced */
  uint64_t sequence_errors; /* header/sequence/length mismatches */
  uint64_t ring_overruns; /* DMA lapped the capture thread: frames lost */
  uint64_t tx_underruns;  /* MM2S ring ran empty while injecting */
  uint32_t mic_overruns;  /* front-end OVERRUNS register (dropped mic samples) */
  uint32_t status;        /* last front-end STATUS */
};

struct fe_mic {
  uint32_t peak;        /* absolute 18-bit peak */
  int32_t dc;           /* window sum / 65536 */
  uint64_t mean_square; /* window sum of squares / 65536 */
};

struct fe {
  struct plio_uio regs, reset, irq;
  struct plio_dma dma;
  struct plio_buffer tx, rx;
  uint32_t control;
  struct fe_config config;
  int running;
  /* injection (MM2S) state, owned by the injection thread */
  unsigned tx_head, tx_tail; /* submitted / reclaimed descriptors */
  uint64_t tx_samples;       /* samples submitted since start */
  uint64_t tx_start_ns;      /* first descriptor handed to the DMA */
  /* capture (S2MM) state, owned by the capture thread */
  unsigned rx_pos;
  uint32_t rx_consumed, next_sequence;
  struct fe_stats stats;
  _Atomic uint64_t tx_submitted; /* samples visible to the capture side */
};

int fe_open(struct fe *);
void fe_close(struct fe *);
/* Stop, reset the DMA and the PL front end, rebuild both descriptor rings.
 * Sequence numbers restart at zero. Configuration must be applied again. */
int fe_reset(struct fe *);
int fe_configure(struct fe *, const struct fe_config *);
/* Start the cyclic S2MM ring and enable the front end. */
int fe_start(struct fe *);
/* Pause the front end (does not flush). Use fe_reset to start a new clip. */
void fe_stop(struct fe *);

/* Injection thread. Samples are signed 18-bit in int32 (int16 * 4). Copies
 * into free MM2S chunks and queues them; blocks on the MM2S IRQ while the
 * ring is full. Returns samples queued (== n unless timeout/error -> -1). */
long fe_inject(struct fe *, const int32_t *samples, size_t n, int timeout_ms);
/* Wait until every queued sample has been read by the DMA. */
int fe_inject_drain(struct fe *, int timeout_ms);
/* Frames the front end will produce from n samples after reset. */
static inline uint64_t fe_frames_for(uint64_t n) { return n < 320 ? 0 : (n - 320) / 160 + 1; }

/* Capture thread. Waits for the S2MM IRQ, returns 1..max frames, 0 on
 * timeout, -1 on error (EIO DMA error, EOVERFLOW ring overrun, EPROTO bad
 * sequence/header). Frames are checked against the expected sequence. */
int fe_capture(struct fe *, struct fe_frame *out, size_t max, int timeout_ms);
uint32_t fe_hw_frames(struct fe *); /* FRAMES register */

void fe_led(struct fe *, int on);
/* Single UI owner, permitted alongside fe_inject/fe_capture. No reset,
 * configure, start, stop or legacy LED calls may overlap. Updates only the
 * LED control bit; consumes only the hardware-debounced sticky button bit.
 * Returns 1 for a press, 0 otherwise. Does not change stats or IRQ arming. */
int fe_ui_poll(struct fe *, int speaking);
int fe_button(struct fe *);  /* sticky debounced press flag (STATUS bit3); see fe_clear */
int fe_button_wait(struct fe *, int timeout_ms); /* 1 press, 0 timeout */
uint32_t fe_status(struct fe *);
void fe_clear(struct fe *, uint32_t bits);
void fe_mic_read(struct fe *, unsigned mic, struct fe_mic *);
#endif
