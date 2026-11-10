# KV260 platform

The platform owns `hw/`, `sw/plio/`, board helpers in `tools/`, and this document.
Source requirements: proposal Tables V/VI/IX and Fig. 2. PL clock is 100 MHz;
HP0 is a noncoherent 128-bit DDR path. AXI streams preserve TKEEP and TLAST.

## Build and deploy

From the repository root in PowerShell:

```powershell
./hw/build.ps1 loopback
./tools/deploy.ps1 loopback
./tools/board.ps1 put tools/setup-buffers.sh stt/setup-buffers.sh
./tools/board.ps1 run -Sudo 'sh /home/ubuntu/stt/setup-buffers.sh'
./tools/board.ps1 put sw/plio stt/
./tools/board.ps1 run -Sudo 'set -e; cd /home/ubuntu/stt/plio; make; ../build/sw/plio/tests/buffers; ../build/sw/plio/tests/loopback'
```

Vivado/bootgen 2026.1 must be installed at the paths in the scripts; WSL FedoraLinux-44
needs `dtc` (`dnf install dtc`). The managed host sandbox requires elevated execution
for WSL, board networking and Vivado license access. A sandboxed Vivado reports a
missing license even though elevated Vivado detects the installed BASIC license.
Generated files stay in ignored `build/hw/loopback/`; source control contains Tcl,
DTS and scripts only. `hw/package.ps1 loopback` repeats packaging without rebuilding.
The build fails on incomplete synthesis/implementation, absent timing paths or
negative setup slack. Inspect `timing.rpt` for hold and unconstrained-path warnings.

Board helpers read only `.board.env` (BOARD_HOST, BOARD_USER, BOARD_PASS,
BOARD_HOSTKEY); keep this file gitignored. `-Sudo` uses the same password without
an interactive prompt. No authentication changes or SSH key installation are needed.
Deployment copies only our `stt-*` directory. It unloads the current application;
pre-existing firmware files are never changed. Stop all users of UIO before reload.

## Address map and IRQs

| Block | Base | Length | UIO name | GIC SPI |
|---|---|---|---|---|
| AXI DMA MM2S | 0xA0000000 | 64 KiB | stt-dma-tx | 89 |
| AXI DMA S2MM IRQ endpoint | same | same | stt-dma-rx | 90 |
| GPIO LED/channel 1, button/channel 2 | 0xA0010000 | 64 KiB | stt-gpio | none |
| AXI Timer | 0xA0020000 | 64 KiB | stt-timer | 91 |

Concat inputs 0,1,2 map to pl_ps_irq0[0:2]. Interrupts are level high. The DMA
has two UIO endpoints for separate interrupt file descriptors and one shared
register aperture. One process must own both; do not use a kernel AXI DMA driver
on these register blocks. UIO indices are discovered by sysfs name, never hardcoded.
GPIO data is offset 0 (LED), channel 2 data offset 8 (button).
Only LED C11 and button B11 constraints are copied from rtl/kria-constraints.xdc for
loopback. Reset is synchronized by proc_sys_reset. The overlay sets FCLK0 and AFI.

## Add an RTL IP and its DMA

The module must expose Vivado-inferred `s_axi` (AXI-Lite), `s_axis`, `m_axis`
(AXIS with TKEEP and TLAST), `aclk`, active-low `aresetn`, and a level `irq`.
Use X_INTERFACE_INFO/X_INTERFACE_PARAMETER attributes when inference is ambiguous.

1. Add `add_files [glob $root/rtl/<block>/*.sv]` before module creation in create.tcl.
2. Increase ctrl NUM_MI from 3 to 5, mem NUM_SI from 2 to 4, irqs NUM_PORTS from 3 to 6.
3. Before `assign_bd_address`, call:

```tcl
add_rtl_pipeline frontend stt_frontend M03_AXI M04_AXI S02_AXI S03_AXI In3 In4 In5 0xA0030000 0xA0040000 32 s_axis_samples m_axis_features
```

For the current front end, also `add_files [glob $root/rtl/frontend/*.mem]` and expose its PMOD ports. Its LED/button replace the loopback GPIO ownership. The helper lives in `hw/platform.tcl`; the final three arguments select stream width and interface names (defaults: 128, s_axis, m_axis). DDR remains 128-bit. `hw/check_interfaces.tcl` checks the current front-end interface without synthesis.

4. Add matching generic-uio nodes for the IP (SPI 92) and DMA TX/RX (SPIs 93/94)
   to the overlay, with bases 0xA0030000 and 0xA0040000. Add named port constraints
   to the build's allowlist if the new module exposes external ports.
5. Add the design name to the Tcl design selection, then build and deploy it.
   Ensure software discovers the new UIO names and creates one DMA owner per block.

`add_dma` configures simple mode, 64-bit addresses, 26-bit length and both DREs.
Scatter-gather descriptors/chaining are not implemented. Double buffering is a
caller policy: two separate source/destination pairs, each with exclusive ownership. `plio_dma_start_sized` takes separate source length and destination capacity, and `plio_dma_received_bytes` returns the completed packet size.

## DMA buffers and cache policy

Pinned upstream ikwzm/u-dma-buf commit:
`2cdfd21bb3decb7be02ccf7b5d21c33c8b92b5b0`. Kernel headers for
6.8.0-1036-xilinx were already installed, and the module builds natively with GCC 13.
`setup-buffers.sh` builds and loads two 16 MiB module-parameter allocations. This
avoids allocating/releasing CMA memory on every firmware overlay reload. The board's
`/dev/udmabuf` is the unrelated kernel memfd driver and is never used. DMA heaps
do not by themselves expose a safe userspace physical-address API.

No system startup files are installed. After each reboot, run the setup command
above (everything persists under ~/stt). Rebuild after a kernel update. Allocation
can fail if CMA is fragmented; allocate early after boot. Actual addresses come
from sysfs; they are not constants. Root is used for sysfs sync and UIO access.

Open without O_SYNC: cached mapping, quirk_mmap_mode=2, sync_mode=1,
dma_coherent=0. Setup selects whole-buffer BIDIRECTIONAL maintenance. Library
`plio_buffer_device()` flushes/invalidate before handing either buffer to hardware;
`plio_buffer_cpu()` invalidates before CPU consumption. Never touch a buffer while
PL owns it. These calls operate on the whole 16 MiB allocation, so small transfers
pay avoidable overhead; use `plio_buffer_sync_range()` for small packets (64-byte aligned offset and length). Single owner,
no internal locking. POSIX memory fences accompany MMIO and ownership transfers.

## C API

Include plio.h and link libplio.a (`make -C sw/plio`). Functions return 0 on success
or -1 with errno, except the nanosecond clock. Caller owns the structs.

- `plio_uio_open/close`: sysfs name discovery, fd and mmap lifetime.
- `plio_irq_enable/wait`: write 1 to arm; poll/read cumulative UIO count with timeout.
  Clear the IP's level source before rearming. Delta wraps modulo 2^32.
- `plio_buffer_open/close`: mmap preallocated buffer index, physical address and size.
- `plio_buffer_device/cpu`: whole-buffer ownership/cache transitions; `plio_buffer_sync_range` maintains a 64-byte aligned range.
- `plio_dma_open/close/reset/start/wait`: RX-first programming, 64-bit addresses,
  length validation, IOC/error IRQs, bounded wait, and reset after errors/timeouts.
  Wait reports EIO for device errors, ETIMEDOUT for deadline expiry; reset failure
  requires the caller to stop and reload firmware. No thread may share a DMA handle. `plio_dma_open_named` accepts the TX/RX UIO names for additional IP instances.
- `plio_now_ns`: CLOCK_MONOTONIC timestamps.

## Validation

Measured results and exact commands are in the Platform section of results.md.
Loopback test uses deterministic golden byte vectors, odd lengths and a trailing
sentinel, then decode-error injection, recovery, and skipped-maintenance comparison.
The test's bandwidth excludes cache preparation and includes driver submission/wait;
it reports simultaneous read/write payload rate, not isolated-channel bandwidth.
The timer auto reloads every 1,000,000 clocks. The test corrects per-interrupt monotonic intervals using the live timer phase to detect coalesced hardware periods. It also rejects UIO count gaps and service intervals over 15 ms. Aggregate monotonic drift is diagnostic only. A userspace scheduling stall can correctly fail the test.

To launch the 30-minute soak under nohup (run as root through board helper):

```sh
cd /home/ubuntu/stt/plio
nohup sh -c '../build/sw/plio/tests/irq_soak 1800 > /home/ubuntu/stt/irq-soak.log 2>&1; echo $? > /home/ubuntu/stt/irq-soak.exit' < /dev/null > /home/ubuntu/stt/irq-launch.log 2>&1 &
```

Read irq-soak.log and irq-soak.exit after completion; starting it is not a pass.
Power: `sh ~/stt/power.sh 100 0.1`, board input INA260 U14 in microwatts. Override
STT_POWER_INPUT if the hwmon inventory changes. Samples use fixed sleep intervals
plus sysfs overhead, rather than a hard real-time scheduler.




Final real-board acceptance: 1.562481 GB/s in each direction simultaneously
(3.124962 GB/s aggregate); all golden sizes and error/reset recovery pass;
skipped maintenance demonstrably produces stale data. The 30-minute timer run
passes at 1800.004622 s / 179998 IRQs, zero missing hardware periods or UIO gaps,
worst service interval 10.737014 ms. Raw logs and timing/resource reports are
checked into results. The timer stops at test completion; stt-loopback
remains loaded. Physical button presses and microphone I/O were not tested.

For the current front end, submit one output frame per DMA transaction: first
320 samples (1280 TX bytes), then 160 samples (640 TX bytes) per hop, with a
520-byte RX capacity. Use start_sized and maintain ranges rounded up to 64 bytes.
Long input streams producing multiple TLAST packets need a caller that rearms
RX between packets; the paired wait API waits for both channels and is intended
for one request/response transaction. Scatter-gather and queued asynchronous
streaming are integration work, not implemented acceptance claims.

## stt-fe design (front end, integration test I1)

Build and deploy: `./hw/build.ps1 fe`, `./tools/deploy.ps1 fe` (app `stt-fe`).
It contains everything in stt-loopback (loopback DMA, GPIO, timer) plus
stt_frontend and a scatter-gather AXI DMA with 32-bit streams. The GPIO's LED
and button pins are owned by stt_frontend; GPIO channel 1 instead drives an
active-high `proc_sys_reset` aux input that resets only stt_frontend (sequence
numbers, framing and history restart at zero), and channel 2 still reads the
button. The I2S, LED and button pins come from rtl/kria-constraints.xdc, so the same
bitstream supports the SPH0645 microphones; PMOD I/O is false-pathed (two-flop
synchronized inputs, 1 MHz register outputs). `add_dma`/`add_rtl_pipeline`
take an optional final SmartConnect port that enables scatter-gather and
connects M_AXI_SG; loopback calls them unchanged (simple mode).

| Block | Base | Length | UIO name | GIC SPI |
|---|---|---|---|---|
| Loopback DMA MM2S / S2MM | 0xA0000000 | 64 KiB | stt-dma-tx / stt-dma-rx | 89 / 90 |
| GPIO: ch1 front-end reset, ch2 button | 0xA0010000 | 64 KiB | stt-fe-reset | none |
| AXI Timer | 0xA0020000 | 64 KiB | stt-timer | 91 |
| stt_frontend registers (frontend_spec.md) | 0xA0030000 | 64 KiB | stt-frontend | 92 |
| Front-end SG DMA MM2S (samples) | 0xA0040000 | 64 KiB | stt-fe-tx | 93 |
| Front-end SG DMA S2MM (features) | same | same | stt-fe-rx | 94 |

mem SmartConnect: S00/S01 loopback DMA, S02/S03 front-end MM2S/S2MM, S04 SG
descriptor master. ctrl: M00 loopback DMA, M01 GPIO, M02 timer, M03 front end,
M04 front-end DMA. Concat In0..In5 = SPI 89..94.

### sw/fedrv driver

`make -C sw/fedrv` builds `libfedrv.a` (with plio) and `fetool`. Memory: udmabuf0
holds the MM2S descriptor ring (128 x 64 B) and 128 sample chunks of 4096
signed-18 samples from offset 64 KiB; udmabuf1 the cyclic S2MM ring (1024
descriptors) and 1024 frame slots of 576 B from 64 KiB (10.24 s of frames).
Only touched descriptors/slots are range-synced. No per-frame rearm: S2MM runs
in cyclic BD mode with IRQ coalescing (IRQThreshold = N frames); MM2S is a
chained descriptor ring advanced by TAILDESC writes, so long WAVs stream
continuously, and an idle engine resumes on the next TAILDESC write.

- `fe_open/fe_close`, `fe_reset` (stop, DMA reset, PL front-end reset, rebuild rings),
  `fe_configure(struct fe_config)`: source (`FE_SRC_MIC1/MIC2/BEAM/INJECT`), `paced`
  (16 kHz) or bulk, `irq_frames` 1..255, I2S right channel, button IRQ, beam delays/gain.
- `fe_start/fe_stop` (pause; reset begins an independent clip).
- Injection thread: `fe_inject(samples, n, timeout)` copies into free chunks and blocks on
  the MM2S IRQ when the ring is full; `fe_inject_drain`; `fe_frames_for(n)`.
- Capture thread: `fe_capture(out, max, timeout)` sleeps on the S2MM IRQ, reads the
  FRAMES register, checks descriptor completion (status written after data), then
  invalidates and copies the frame slots, checking length 520, exponent -42 and the
  expected sequence (EPROTO), DMA errors (EIO) and ring laps (EOVERFLOW, counted in
  `stats.ring_overruns`). Frames carry `irq_ns` and `avail_ns` timestamps.
- `fe_led`, `fe_button` (sticky press flag), `fe_button_wait` (stt-frontend IRQ),
  `fe_status/fe_clear`, `fe_mic_read` (peak, DC, mean square per microphone).
- `stream.h`: `fe_stream_start(fe, fill_cb, ctx)` runs the IRQ-driven capture thread
  and, with a fill callback, an injection thread; `fe_stream_next` pops a lock-free
  SPSC ring (4096 frames; a full ring drops and counts). Mic streams end with
  `fe_stream_stop`; injected streams end after the frames of all injected samples.
- `features.h`: PS log/normalise (utterance and running Welford with prior), ported
  operation-for-operation from model/frontend.py incl. NumPy float32 pairwise sums,
  and the model INT8 quantiser.

One injection and one capture thread may run concurrently; all other calls belong
to the owner thread. Results: docs/results/i1.md.

Board validation after the November 2 reboot found a blocking fe DMA integration
issue: optional SG status/control streams are enabled in the generated DMA IP,
but S_AXIS_STS has TVALID tied low. The bounded `tests/i1/repro_i1.c`
test receives zero completed frames from 4096 injected zero samples, while the
front end reports two packets. See docs/results/i1.md for commands and registers.
The fe SG DMA needs its unused status/control streams disabled and a rebuilt
bitstream validated before acceptance. This does not affect the simple-mode
stt-loopback design. Front-end registers currently expose no version/ID register.

### SG tail doorbell order (I1 follow-up, November 2/3)

With `c_addr_width=64`, write TAILDESC low word first, then its MSB: the
MSB write triggers the SG fetch. `sw/fedrv` previously reversed this order,
fetching a zero-length unsubmitted TX BD and raising DMAIntErr. The fix passes
two consecutive 4096-sample / 24-frame zero-input replays with both channels
clean, and all six I1 clips in bulk and paced modes (9,132 frames total).
Run `make -C sw/fedrv check-tail` for the host fake-register regression; the
bounded board test is `tests/i1/repro_i1.c`. Full commands and numerical
results, including PS float/INT8 discrepancies, are in `docs/results/i1.md`.
No loopback configuration or front-end RTL changed.

## stt-full design (I2)

Build `./hw/build.ps1 full`, deploy `./tools/deploy.ps1 full`. Uses the frozen
engine RTL in this checkout, plus the complete stt-fe design above. All logic
uses 100 MHz pl_clk0; HP0 remains noncoherent. Engine DMA is simple mode,
128-bit AXIS with TKEEP/TLAST, 64-bit addresses, 26-bit lengths and DRE.
Front-end SG status/control streams remain disabled.

| Block | Base | Length | UIO name | GIC SPI |
|---|---|---|---|---|
| Engine AXI-Lite | 0xA0050000 | 64 KiB | stt-engine | 95 |
| Engine DMA MM2S | 0xA0060000 | 64 KiB | stt-engine-tx | 96 |
| Engine DMA S2MM | same | same | stt-engine-rx | 104 |

All stt-fe UIO names, addresses, SPIs 89..94 and PMOD pins are preserved.
IRQ0 concat In6/In7 carry engine/engine MM2S; IRQ1 concat In0 carries engine
S2MM (ZynqMP's second PL IRQ bank begins at SPI 104, not SPI 97).
ctrl M05/M06 = engine/engine DMA; mem S05/S06 = engine MM2S/S2MM.
Engine CONTROL soft reset aborts the engine; driver recovery resets both DMA
channels as required by docs/engdrv.md.

`tools/setup-buffers.sh` now allocates 0/1 = 16 MiB each (unchanged front end),
2 = 32 MiB TX (holds the resident ~20 MB RUN_NETWORK program) and 3 = 1 MiB RX. It validates all four devices and configures
whole-allocation bidirectional maintenance. Existing two-buffer module loads
must be stopped/unloaded before rerunning setup; the script fails rather than
unloading buffers that another process might own. Drivers use range sync.
Use `flock /home/ubuntu/stt/board.lock ...` for deployment and measurements.
Measured acceptance and limitations: docs/results/i2.md.

stt-full board acceptance (2026-11-03): WNS 0.000 ns / WHS +0.011 ns after
one AggressiveExplore post-route pass (no RTL/clock changes), 53,652 LUT,
59,890 FF, 42.5 BRAM tiles, 52 URAM, 593 DSP. Deployed successfully. All UIO
map0 sizes are 64 KiB; observed GIC IDs 127/128/136 confirm engine SPIs
95/96/104. I2: 10 windows, 27,840 identical logit bytes, 10 identical CTC
sequences; frontend bulk/paced replay each 772 bit-exact mel frames.
Recovery/timeout and cache-maintenance tests pass. A 3 s window in per-layer
mode takes 2284.378 ms (2047.060 ms engine preparation, overlapping DMA).
See results/i2.md and results/i2 for commands, raw logs and limitations.

## Fast engine deployment (2026-11-05)

Deployed the b1cb4f5 RTL package from branch t3/fpga-integration-tests,
package commit d51f384, using `./tools/deploy.ps1 full`. Before deployment,
`flock -n /home/ubuntu/stt/board.lock true` succeeded and `ps` showed no
sttcap, i2_probe, fetool, engtool, irq_soak or loopback processes. No process
was killed. xmutil reports stt-full loaded. Engine VERSION and RUN_NETWORK
capability are checked by the subsequent locked I2 probe.

SHA256 stt-full.bit.bin:
`aeaf12b88a5bbd9c5edd66c6da92c344ca24a2f8655787ac3c319ea6644ab3ea`.
SHA256 stt-full.dtbo:
`0f30599fe59d81372ff17dcad1ec589d9cdf9c0ed7716dcfd089371f6e65089e`.
Routed timing: WNS +0.521 ns, WHS +0.010 ns, 100 MHz (build/hw/full/timing.rpt;
committed copies `results/full-{timing,utilization,power,route-status}.rpt`).
To resume an interrupted stt-full implementation from its post-place
checkpoint, run `vivado.bat -mode batch -source hw/route_full.tcl`.

Post-load sysfs confirms 64 KiB apertures: engine A0050000, engine TX/RX
A0060000, frontend A0030000, frontend TX/RX A0040000. /proc/interrupts
reports GIC IDs 127/128/136 for engine control/TX/RX respectively
(engine=127, TX=128, RX=136), matching SPI 95/96/104. u-dma-buf 2 is
33,554,432 bytes, sufficient for the resident program. The worktree's
.board.env already exists, is ignored, and is never staged.

RUN_NETWORK acceptance now passes on engine 0x00010002 with the 32 MiB TX
buffer: 13 windows / 69,136 padded logit bytes equal qnrt, including 640/656
and 1552-frame fallback. The minimal driver fix e6b6123 resets the parser
after DMA stops during recovery. Steady-state paired 3 s timing (20 pairs):
CPU 217.527 ms median, PL 103.976 ms, 2.092x. Frontend bulk/paced mel
remains bit-exact. Full evidence: docs/results/i2.md, results/i2-network.

T1 paced paired A/B (2026-11-05): all 40 clips / 80 runs complete, finals
identical on all 40 pairs; 39/848 word errors (4.5991%) on each backend.
CPU/PL preview latency p95 at framebuffer completion: 770.813/240.175 ms;
PL median/max 238.669/242.881 ms. All latency samples use the valid FIFO
first-FFT anchor. PL preview compute p50/p95: 103.849/103.921 ms, zero merged
updates or lost stitch frames. CPU merges 1,235 updates. Committed-preview
WER is CPU 6.2500%, PL 7.3113%; the different served-tick populations remain
a preview-quality issue, despite equal finals. Board power CPU/PL sampled
mean: 3.972/3.533 W, max: 4.520/4.300 W. PS sttcap CPU mean is 273.204% /
10.666% (100% = one A53 core). Measured core thresholds pass; 3x speed-up
stretch fails, while measured latency, finals WER and sampled power meet their
stretch thresholds. FIFO continuity/anchor uncertainty, physical scanout,
L_word, live-microphone/corpus generalisation and T2 remain unvalidated.
Commands, complete tables and evidence: docs/results/t1.md and
results/t1. Board remains loaded with stt-full and test lock released.

## stt-full150 design

Build `./hw/build.ps1 full150`, package `./hw/package.ps1 full150`, deploy
`./tools/deploy.ps1 full150` only while holding `~/stt/board.lock` and after
checking for other board users. This is an independent variant of stt-full.
The front end and its SG DMA, loopback, GPIO, timer, and HPM0 remain at
100 MHz pl_clk0. Engine, engine simple DMA, both engine AXIS links and HP0
run at 150 MHz pl_clk1 with a dedicated proc_sys_reset. Both SmartConnects
have two clocks and provide the AXI crossings. Engine DMA AXI-Lite is also
150 MHz; the controlling HPM0 remains 100 MHz. UIO names, addresses, IRQs
and PMOD pins match stt-full. The overlay requests both PS clock rates.
No user asynchronous-clock groups are introduced. Detailed clock interaction,
CDC and per-clock/pair reports are generated in `build/hw/full150/`.
See [full150 results](results/full150.md) for build and board acceptance status.

stt-full150 acceptance (2026-11-05): routed 100 MHz WNS/WHS +2.910/+0.010 ns,
150 MHz +0.282/+0.010 ns, zero unconstrained internal endpoints; 68,642 LUT,
73,297 FF, 74.5 BRAM, 60 URAM, 843 DSP. Generated SmartConnect handshakes and
asynchronous FIFOs provide CDC; vendor reset-fanout and existing PMOD warnings
are explicitly reviewed in the results document. No new clock-group exceptions.
I2 passed 12 windows / 54,694 logit bytes / 12 CTC sequences, with ten network
windows and two long-window fallbacks. Twenty paired exact 3 s windows measured
PL median/p90 69.608/69.748 ms, CPU 218.566/219.783 ms (four threads), median
paired speed-up 3.141x. Bulk/paced replay each preserved 772 bit-exact mel frames.
The board was restored to stt-full (100 MHz) before releasing the session lock.
