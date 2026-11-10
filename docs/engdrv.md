# PS-PL engine driver

`sw/engdrv` implements the QNC2 RUN_LAYER and RUN_NETWORK contracts through
one shared command encoder and response validator. `engdrv.h` is the C ABI; C++ callers are supported.
Build the board archive with `make -C sw/engdrv` (natively on the KV260 or with
any aarch64 C11 compiler; it needs `sw/plio`, `sw/app/backend.h` and, for
`i2`, `sw/qnrt`). Build the host RTL adapter with `make -C sw/engdrv cosim`
(Verilator and its development headers required). `make rtl-snapshot` followed
by `make cosim RTL=../../build/sw/engdrv/rtl/rtl/engine` builds the adapter from the
committed engine RTL (`git archive HEAD`), isolating driver tests from
in-progress engine edits; the commit is written to `build/sw/engdrv/rtl/COMMIT`.
The cosim compile needs Verilator's `include/vltstd` (svdpi.h) on the include
path; the Makefile adds it.

`tools/test_cosim.py` is the driver regression (`make -C sw/engdrv check` runs
`--misc`, a T_in=48 network and all manifest vectors in `SHARDS` processes):
`--misc` covers program validation, malformed packets, recovery, single-channel
depthwise, async EBUSY/poll and host timeout/reset; `--vectors K/N` sends every
N-th manifest vector through `engdrv_run_layer`; `--network CLIP:START:T:VALID`
(or `CLIP:full`) runs a real saved C1 feature window through `engdrv_run_network`
and compares every padded logit byte with `qnrt_run_frames`, `golden_int8.network`
and, for full clips, the stored manifest logits. `--mode auto|layer|network`
selects the network executor (`engdrv_set_mode`); `catA+B[+...]` windows
concatenate the valid frames of several clips (padded to 16) to exercise
inputs beyond 800 frames. `--network-misc` covers RUN_NETWORK malformed
packets, recovery, host timeout and mode fallback. Per-vector and per-layer
cycle CSVs go to `build/sw/engdrv/measurements/`. Results: `docs/results/engdrv.md`.

Open with `engdrv_config`: select cosim or board, distinct TX/RX u-dma-buf indices,
and a positive per-command timeout in milliseconds. Close joins any async worker.
Use one context per caller; never mix synchronous operations with an outstanding
async request. Separate contexts need separate board DMA devices/buffers; the
current fixed UIO names support one board context exclusively.

`engdrv_version`, `engdrv_ping`, and `engdrv_recover` provide control operations.
`engdrv_load` takes the QNP2 file produced by `model/qnc2_export.py`, validates
record sizes, operations, geometry, source ordering and requantization arrays,
and replaces the previous program only on success. It does not consume QNW1.
The layer index is zero based. `engdrv_run_layer` takes channel-major caller
buffers, allocated/valid frame lengths and input signedness. Output capacity is
in bytes; out must not alias inputs. Signed INT8 and UINT8 tensors share the byte
ABI. Add requires both signed branches. Lengths follow the convolution formula;
zero valid length is supported. Logits and intermediate values are never decoded
as float.

`engdrv_run_network` takes signed channel-major [64,T] input (1..2048 frames,
`ENGDRV_MAX_FRAMES`) and returns a context-owned signed [29,Tout] tensor,
allocated and valid lengths. It has two executors with identical results:

- **RUN_NETWORK** (one command per call): header + padded features, followed
  by the loaded QNP2 program, as one AXIS packet; one response carrying only
  the logits. Used when the engine VERSION is >= 0x00010002, the program is
  the fixed 186-record graph ending in signed 29-channel logits, the transport
  holds the program resident (below), and the window fits the network limit:
  1..640 allocated input frames and every tensor <= 320 frames.
- **Per-layer** (RUN_LAYER graph, 186 commands): intermediate tensors
  round-trip through host memory. Layers whose allocated input or output
  exceeds the 800-frame RUN_LAYER limit (C1 for T_in > 800) are time-tiled
  exactly: each slice carries its convolution halo, is masked at the source
  valid length on the host, and runs with padding 0 and valid = slice length;
  add slices need no halo. A 1552-frame input is 188 commands.

`engdrv_set_mode(e, ENGDRV_AUTO|ENGDRV_LAYER|ENGDRV_NETWORK)` selects the
executor. AUTO (default) uses RUN_NETWORK when possible and otherwise falls
back to per-layer: older bitstreams (0x00010001), a TX buffer too small for
the resident program (ENOSPC at load), and long finals (T_in > 640). LAYER
forces per-layer; NETWORK fails with the load errno (e.g. ENOTSUP, ENOSPC)
or E2BIG instead of falling back. `engdrv_network_capable` reports whether
RUN_NETWORK is usable for the loaded program. An engine error status
(including descriptor rejection) is returned as EPROTO; there is no silent
retry in the other mode.

`engdrv_network_timing(e, i, &t)` returns per-command timing of the most
recent successful network run: one entry per layer in per-layer mode (tiled
layers sum their commands), a single entry (index 0) for RUN_NETWORK. The
network timing's cycles equal the sum. `engdrv_last_run` returns the mode,
command count, wall time, summed transport exchange time, time outside the
exchanges (graph walk, encoding, tiling, copies) and range cache-maintenance
time/calls. PING accepts engine versions 0x0001000x.

`engdrv_submit` copies input and starts a pthread worker. Only one request is
outstanding; a second submit returns EBUSY. `engdrv_poll` returns 0 pending,
1 ready, -1 error; timeout 0 is nonblocking and negative waits indefinitely.
After ready, the context owns logits until the next inference or destruction.
Direct API calls return 0 success/-1 failure with errno. Raw `engdrv_exchange`
returns a valid error packet successfully; inspect timing.status. Layer/network
calls reject nonzero engine status. Raw exchange requires a beat-aligned packet.

## Transport and memory ownership

RUN_NETWORK keeps the ~20 MB program out of the per-call path. At
`engdrv_load` the transport's `program()` stores it once: the board copies it
to the page-aligned end of the TX u-dma-buf and cleans that range once
(re-loading returns the old range to the CPU first); cosim keeps a copy. Each
call then writes only the 64-byte header and features immediately below the
program, so simple-mode DMA sends header+features+program as one MM2S
transfer of `64 + align16(64*T_in) + program_bytes`; in SG mode two
descriptors (header+features with SOF, then the program with EOF) form one
packet. Per call, range maintenance covers only header+features, the two SG
descriptors and the response (`64 + align16(29*ceil(T_in/2))`): four sysfs
range syncs. Per-layer packets are confined below the network header area.
The TX buffer must hold `program + 64 + 64*640 + 128` bytes plus 4 KiB
alignment: about 20.1 MB for the current program, so **u-dma-buf 2 must be at
least 24 MiB (32 MiB recommended)**. The present 8 MiB allocation makes
`program()` fail with ENOSPC, and AUTO runs per-layer. The RUN_NETWORK
transport is cosim-verified only: no bitstream with engine 0x00010002 exists
yet, and the SG two-descriptor path has never run on hardware (stt-full uses
simple-mode DMA).

The board uses `plio` for UIO mappings, DMA reset/start/IRQ waits, monotonic time,
and u-dma-buf range cache maintenance. Default app buffer indices are 2 and 3,
leaving frontend 0/1 alone. Supply dedicated physically contiguous buffers;
8 MiB TX and 1 MiB RX cover supported layer commands (including descriptor and
cache-line rounding). No frontend buffers may be shared during operation.

Simple DMA places command/response at offset zero. SG mode is detected from
DMASR.SGIncld and uses one 64-byte descriptor at offset 0, stream data at offset
64, self-linked next pointers and one packet per channel. TX SOF/EOF are set;
RX capacity includes the response header and padded output. SG channels reset
between commands. This intentionally favors correctness over overlap.

Before launch, copy and flush the TX range and hand the RX range to the device
(invalidate/flush through the bidirectional plio range operation). All sync
ranges start and end on 64-byte boundaries. SG descriptors are included. Arm
S2MM before MM2S. Program 64-bit descriptor addresses LSB before MSB; notably the
TAILDESC MSB triggers fetching. Wait on DMA completion/error IRQs through plio,
then invalidate RX and return TX ownership to CPU before reusing it. SG checks
both descriptor completion/error words; simple mode reads actual S2MM length.
Finally validate QNS2 magic, version, tag, shape, valid length, signedness and
exact packet byte count before copying tensors to caller memory.

On a transport timeout/error or malformed response, pulse engine CONTROL reset
and reset both DMA channels before reuse. Engine settings survive reset; enable
completion/error and W1C pending engine IRQs. Protocol errors drained through
TLAST can recover with the next command. Error errno values include ETIMEDOUT,
EIO, EPROTO, EINVAL and ENOSPC. Hardware reset/cache failure must be investigated
before reusing the context; there is no automatic retry of inference.

## IRQs and timing

Board IRQ wakeups are the DMA MM2S and S2MM IRQs. The engine IRQ is level-latched
and acknowledged by W1C; it need not wake the host separately. Cosim enables and
checks the actual engine IRQ after response TLAST, then acknowledges via
AXI-Lite. Cosim models AXIS ready/valid, full TKEEP/TLAST and independent AXI-Lite
AW/W acceptance against the real Verilator top. It does not model AXI DMA or
cache behavior. Its clock is advanced synchronously by the host caller.

Timing records monotonic submit, completion IRQ observed, and CPU data ready
nanoseconds. Board irq_ns is observed after both DMA waits, not a kernel IRQ
entry timestamp. Cycles are the engine response-header preparation counter;
starvation and issued terms are also exposed. Network timing sums preparation
cycles and reports the last command IRQ. Cosim wall time is simulator cost and
must never be treated as board inference performance.

## Hardware design contract

The hardware integration build must provide exactly this; `board_open` finds
devices by name and fails with ENODEV/ENOENT (backend fails open) otherwise.

|Item|Requirement|
|---|---|
|UIO `stt-engine`|map0 = `stt_engine` AXI-Lite block, size >= 0x3c (registers in `qnc2_protocol.md`; VERSION at 0x08: 0x00010001 per-layer only, 0x00010002 adds RUN_NETWORK)|
|UIO `stt-engine-tx`|map0 = the engine's AXI DMA register bank (MM2S 0x00.., S2MM 0x30..); IRQ = DMA `mm2s_introut`|
|UIO `stt-engine-rx`|map0 = the same AXI DMA register bank; IRQ = DMA `s2mm_introut`|
|AXI DMA|MM2S -> engine `s_axis`, engine `m_axis` -> S2MM; 128-bit stream, TKEEP and TLAST; 64-bit addressing; buffer length register >= 26 bits (a RUN_NETWORK MM2S transfer is ~20.1 MB); simple or SG mode (SG detected from MM2S DMASR.SGIncld); no DRE needed (buffers are 64-byte aligned)|
|u-dma-buf 2 (`/dev/udmabuf2`)|TX, >= 8 MiB for per-layer mode, >= 24 MiB (32 MiB recommended) for RUN_NETWORK's resident program; physically contiguous; sysfs `sync_*` range attributes|
|u-dma-buf 3 (`/dev/udmabuf3`)|RX, >= 1 MiB, physically contiguous|
|DDR path|DMA masters on a non-coherent HP port; software does all cache maintenance|
|Engine IRQ|Optional: may be wired to a fourth UIO for future use; this driver completes on the DMA IRQs and acknowledges the engine IRQ by W1C|
|Reset|Engine CONTROL bit 0 must abort both streams and drop partial packets; software resets both DMA channels with it|
|Clock|Reported cycles are engine `aclk` clocks; millisecond figures assume the routed engine clock (100 or 150 MHz)|

No fixed physical base address is assumed: UIO map0 supplies it. The frontend's
`stt-dma-tx`/`stt-dma-rx`/`stt-frontend` UIOs and u-dma-buf 0/1 are untouched
and may coexist. Indices 2/3 are the defaults of `backend_pl.c` and `i2`;
other callers pass them in `engdrv_config`.

## Application integration

`backend_pl(b, program, scale)` implements the cap_backend submit/poll/destroy
ABI on a QNP2 program. QNP2 has no scale field, so the caller passes the input
quantization scale; sttcap reads it from the matching QNW1 blob (float32 at
byte 32), the value the CPU backend uses. The backend uses board buffers 2/3,
a 10-second per-command timeout, and `ENGDRV_MODE=auto|layer|network`
(default auto). It reports `pl-network` or `pl-layer` and the command count in
each result. The sttcap PL build defines `HAVE_ENGDRV`, which excludes the
ENOSYS stub in `sw/app/backend.c`, compiles `sw/engdrv/backend_pl.c` and
links `libengdrv.a` plus pthread (docs/app.md).

## I2 saved chunks

After hardware exists, run `bash tests/i2/i2.sh PROGRAM.qnp2 CPU.qnw1 < chunks.tsv`.
Each TSV row is `feature-path<TAB>allocated-frames<TAB>valid-frames`; bytes are
saved signed channel-major [64,T] features. The tool feeds identical bytes to
engdrv and qnrt, compares every logit including padding, then compares greedy
CTC IDs (lowest-index argmax ties, collapse before removing blank 28). Any
mismatch exits nonzero. This implements proposal Table VIII I2 without frontend
float differences contaminating the comparison. Record board IRQ/cache/reset
and soak evidence after the bitstream is available.
