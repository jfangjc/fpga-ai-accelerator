# stt-full150 integration

Source: engine/master `5d7613a`; branch `t3/add-full150-design`.
Vivado 2026.1 BASIC licence; `general.maxThreads=6`, run jobs=6.

## Clocking and reproduction

`./hw/build.ps1 full150` builds and packages the independent `stt-full150`
variant. `stt-full` retains its original single-clock configuration.
PS pl_clk0 (100 MHz) clocks HPM0, control SmartConnect's primary clock,
front end, its DMA, loopback DMA, FIFO, GPIO and timer. PS pl_clk1 (150 MHz)
clocks engine, engine DMA (AXI-Lite, memory masters and both AXIS links),
HP0 and memory SmartConnect's primary clock. A dedicated `engine_rst`
proc_sys_reset synchronizes reset release to pl_clk1.

Both SmartConnects have two clocks. Control crosses from HPM0/100 MHz to
engine registers/150 MHz; memory crosses the existing 100 MHz DMA masters
to HP0/150 MHz. Engine DMA to HP0 stays within 150 MHz. The engine and DMA
level interrupts retain their GIC wiring. No clock-group exceptions are added;
the existing PMOD timing constraints are preserved.

The overlay enables FCLK0=100 MHz and FCLK1=150 MHz, preserving every
stt-full UIO name, register aperture and interrupt number.

`hw/report_full150.tcl` emits clocks, clock interaction, detailed CDC,
timing checks, exceptions and setup/hold paths for every clock pair.
`hw/full150_timing.tcl` permits up to three improving post-route
AggressiveExplore passes. Resume with `hw/route_full150.tcl`; re-optimize
an existing routed design with `hw/repair_full150.tcl`. Both retain mandatory
nonnegative setup and hold checks. Package again with `./hw/package.ps1 full150`.

## Validation status

Two-clock block design validation, routed setup/hold, packaging, board I2 and
paired performance acceptance all passed. Detailed evidence follows.

Initial read-only board check found board.lock held and a T1 sttcap process
active. Deployment waited until both were free. The complete session held
board.lock, passed, and restored stt-full (100 MHz) before releasing the lock.

Clock-generation validation caught the board preset's 1 GHz IOPLL model:
requesting 150 MHz alone yielded 142.856 MHz. The superseded build was stopped
(only this worktree's verified process tree). `full150_ps.tcl` models the board's
observed existing 1.5 GHz IOPLL, using /15 and /10 for exact 100/150 MHz.
The PS initialization metadata also uses legal peripheral divisors; overlay
loading does not execute psu_init or change PS PLLs. Generated PS actual clock
properties now report 100.000000 and 150.000000 MHz. A frequency assertion in
the build prevents silent rounding. The original stt-full PS preset is unchanged.

## Routed build accepted (2026-11-05)

The corrected `hw/build.ps1 full150` completed synthesis, implementation,
bitstream generation, XSA export, bootgen and dtc successfully. No extra
AggressiveExplore closure pass or resume/repair was needed. The standard
Vivado implementation includes its normal placement physical optimization,
pre-route phys_opt and router hold fixes. No RTL or clock exception changed.

| Clock/path | Constraint | WNS ns | WHS ns |
|---|---:|---:|---:|
| pl_clk0, front end/control | 10.000 ns (100 MHz) | +2.910 | +0.010 |
| pl_clk1, engine/DMA/HP0 | 6.666 ns (150.015 MHz) | +0.282 | +0.010 |
| 100 -> 150 MHz CDC datapaths | IP max-delay datapath-only | +7.987 | exempt by IP |
| 150 -> 100 MHz CDC datapaths | IP max-delay datapath-only | +4.367 | exempt by IP |

Vivado rounds the generated 150 MHz timing period down to 6.666 ns, slightly
stricter than 6.666667 ns. TNS/THS are zero; pulse-width slack +1.833 ns.
No missing clocks, unconstrained internal endpoints, timing loops or multiple
clock pins. Only the three existing PMOD inputs and five outputs lack I/O
delays, with their preserved explicit false paths.

Final utilization: 68,642 LUT (58.61%), 73,297 FF (31.29%), 74.5 BRAM tiles
(51.74%), 60 URAM (93.75%), 843 DSP (67.55%). Bitstream generation reports
zero errors and zero critical warnings. Reports and artifact hashes are in
`results/full150/`; generated deploy files remain in `build/hw/full150/`.

## CDC review

Clock interaction identifies both inter-clock directions as max-delay
Datapath Only, supplied by generated IP. Both same-clock directions are clean.
There are no user clock-group constraints and no unconstrained internal paths.
The generated control paths contain `gen_async_conv` handshakes and ASYNC_REG
synchronizers for M05/M06. The memory paths contain asynchronous XPM FIFOs
with Gray-pointer synchronizers and bus-skew constraints. All 51 reported
bus-skew constraints pass; worst slack +5.538 ns.

Detailed report_cdc results are retained without new waivers: CDC-3 info 24;
CDC-6 warning 4 (control read-data buses held by the AXI handshake); CDC-15
warning 1,417 (memory FIFO clock-enable-controlled data paths); CDC-2 warning 3
(existing PMOD button/microphone two-flop synchronizers lacking ASYNC_REG).
The **two CDC-11 critical entries** are the shared 100 MHz rst output fanning
out to SmartConnect M05/M06 reset synchronizers, each depth five, false-pathed
by vendor IP. They are reset distribution, not unsynchronized payload or
control data. Deassertion is synchronized in each destination reset module.
The generated vendor waiver patterns do not match these nested `mi_cdc.m_psr`
paths, so they remain visible. Methodology TIMING-9 summarizes these CDC
structures. No blanket CDC waiver or asynchronous group was added. Adding
ASYNC_REG to the existing front-end input synchronizers would require an RTL
change outside this task's ownership; their existing two-stage topology and
PMOD exceptions were preserved.

## Board acceptance

One exclusive `flock ~/stt/board.lock` session covered firmware installation,
load, tests and restoration. No other worker's processes were stopped.
The session verified the board free again after acquiring the lock. Linux
reported pl_clk0=99,999,999 Hz and pl_clk1=149,999,999 Hz; engine VERSION
0x00010002, PING 15 cycles, RUN_NETWORK capability true. Buffers 0/1 were
16 MiB, buffer 2 was 32 MiB and buffer 3 was 1 MiB; no reallocation was needed.
All stt-full UIO names and apertures were retained.

Both the unmodified `build/sw/engdrv/i2` and `build/tests/full150/full150_probe` independently passed
all **12 windows: 54,694/54,694 padded logit bytes, 12/12 identical greedy CTC
sequences** per executor. Ten windows use one RUN_NETWORK command each,
including three 304/300 windows and one exact 300/300 (3 s) window. Two use
AUTO's per-layer fallback: 784/774 (186 commands), 1552/1552 (188 commands,
including time tiling above the RUN_LAYER 800-frame limit). The T1552 input
repeats saved speech features to exercise the long-window contract; it is
not an independently recorded 15.52 s utterance. The original ten I2 inputs
remain unchanged. Every probe asserts the selected mode and command count.
Raw results: `results/full150/i2.log` and `session.log`.

The front-end replay captured 123,760 samples in bulk and paced modes:
**772/772 frames and 49,408/49,408 mel values per mode, zero mismatches**
against frontend_fixed.py. Both have zero sequence errors, ring overruns,
consumer drops, TX underruns and microphone overruns. Bulk wall 1.506302 s;
paced wall 7.736724 s, mean IRQ interval 10,000.156 us. Running-normalized
INT8 remains exact. The accepted I1 PS floating-point portability difference
is unchanged: utterance INT8 has one element differing by one LSB per mode;
PS normalized floating-point arrays are not bit-identical. This is a mel
hardware and running-INT8 acceptance, not an exact PS float claim.

## Paired CPU versus PL, exact 3 s window

Both executors consumed the same saved channel-major signed [64,300] tensor,
valid=300, and every pair compared all 4,350 output bytes and CTC IDs. CPU
uses qnrt INT8 with four threads. One persistent context per executor, two
warm-up pairs excluded, then **20 measured pairs**, alternating CPU-first and
PL-first order. CPU timing covers qnrt_run; PL timing covers the complete
engdrv_run_network API, including encoding, cache maintenance, DMA, response
checks and copies. Model loading is excluded for both. p90 is nearest rank.

| Measurement | Median | p90 |
|---|---:|---:|
| CPU INT8 wall ms | 218.565730 | 219.783250 |
| PL complete API wall ms | **69.608413** | **69.747615** |
| Paired CPU/PL speed-up | **3.141453x** | 3.153560x |
| Engine preparation ms, cycles / 150 MHz | 68.731980 | 68.732493 |
| Explicit PL range-cache maintenance ms | 0.706644 | 0.793460 |

Preparation overlaps DMA; it must not be added to API latency. Four range
sync operations per RUN_NETWORK replace the per-layer round trips. The small
remaining wall-time excess includes host encoding/copies and DMA/IRQ overhead;
it is not an independently measured pure bus-active DMA interval.
These measurements exceed the 2x core and 3x stretch speed-up targets for
this saved 3 s window. They do not establish an end-to-end captioning latency,
WER, power, live microphone or concurrent front-end/engine stress result.

At exit, the session successfully **restored stt-full (100 MHz)** and then
released board.lock. A separate read-only verification confirmed stt-full in
slot 0, pl_clk0=99,999,999 Hz enabled, pl_clk1 disabled, and the lock free.
Restored stt-full .bit.bin SHA256:
`aeaf12b88a5bbd9c5edd66c6da92c344ca24a2f8655787ac3c319ea6644ab3ea`.
The accepted stt-full150 .bit.bin SHA256 is
`4cd65023c6c2933fd7704fb373b7428450c8ec37dfd90b690e12a66245adc1d2`.

## Reproduce acceptance

Prepare with the main checkout's absolute data path (no copied data/ tree):

```powershell
wsl -d FedoraLinux-44 -- /mnt/c/Users/jfang/Documents/stt-accelerator/.venv/bin/python tests/full150/prepare_full150.py /mnt/c/Users/jfang/Documents/stt-accelerator build/hw/full150-assets
Copy-Item C:/Users/jfang/Documents/stt-accelerator/data/export/quartznet_int8.bin build/hw/full150-assets/cpu.qnw1
tar -cf build/hw/full150-src.tar sw tests/full150/full150_probe.c tests/full150/full150_session.sh
```

After checking board.lock and the requested process inventory are free, stage
that tar under `~/stt/full150/`, extract into `~/stt/full150/src`, stage vector
files into `~/stt/full150/assets`, and stage the three packaged firmware files
into `~/stt/full150/firmware`. Run an LF copy of the session script:

```powershell
./tools/board.ps1 run 'tr -d "\r" < ~/stt/full150/src/tests/full150/full150_session.sh > ~/stt/full150/session.sh'
./tools/board.ps1 run -Sudo 'bash /home/ubuntu/stt/full150/session.sh'
./tools/board.ps1 get stt/full150/results build/hw/full150-board
wsl -d FedoraLinux-44 -- /mnt/c/Users/jfang/Documents/stt-accelerator/.venv/bin/python tests/full150/analyze_full150.py build/hw/full150-board
```

The script acquires the lock nonblocking, rechecks processes (excluding kernel
threads such as [spi2]), and restores stt-full via an EXIT trap even on test
failure. Use a distinct staging directory rather than overwriting another
worker's native driver tree. Front-end comparison uses the existing
`tests/i1/i1.py compare` from the main checkout with capture basenames
`1089-134691-0023.bulk.bin` / `.paced.bin`. JSON summaries, clock observations,
interrupt snapshots, input hashes and logs are committed under
`results/full150/`; raw captures remain in ignored `build/hw/full150-board/`.
No rtl/, sw/, tools/ or docs/results.md files were changed.
