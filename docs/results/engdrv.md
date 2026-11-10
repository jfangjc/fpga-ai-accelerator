# PS-PL engine driver (`sw/engdrv`) results

## RUN_NETWORK driver (2026-11-03, second update)

Driver commits 53ed827 onward, on the engine RTL snapshot of `HEAD`
1129421 (committed engine RTL e082d19, S2b-3; engine VERSION 0x00010002),
built with `make rtl-snapshot` so the in-progress timing-closure edits in
`rtl/engine` were excluded. FedoraLinux-44 WSL, Verilator 5.046.

```
make -C sw/engdrv rtl-snapshot
make -C sw/engdrv cosim RTL=../../build/sw/engdrv/rtl/rtl/engine libqnrt.so
.venv/bin/python sw/engdrv/tools/test_cosim.py --misc
.venv/bin/python sw/engdrv/tools/test_cosim.py --network-misc
.venv/bin/python sw/engdrv/tools/test_cosim.py --mode network --network clip2:full   # etc.
```

Every command exited 0. PASS lines: [`results/run_network_cosim.log`](../../results/engdrv/run_network_cosim.log),
[`results/misc_cosim.log`](../../results/engdrv/misc_cosim.log).

### RUN_NETWORK equivalence (one command per window)

Each window goes through `engdrv_submit`/`engdrv_poll` with `--mode network`;
every padded logit byte and both output lengths are compared with
`qnrt_run_frames` and `golden_int8.network` (full clips also with the stored
manifest logits). Each run is exactly one RUN_NETWORK command.

|Window|T_in / valid|T_out / valid|Logit bytes identical to|Engine cycles|ms @100 MHz|
|---|---|---|---|---:|---:|
|clip2 full|32 / 18|16 / 9|qnrt, golden, manifest|1,757,439|17.57|
|clip0 0:48|48 / 48|24 / 24|qnrt, golden|2,241,214|22.41|
|clip1 full|112 / 102|56 / 51|qnrt, golden, manifest|4,197,894|41.98|
|clip0 0:300|300 / 300|150 / 150|qnrt, golden|**10,067,933**|100.68|
|clip0 0:304 (3 s window)|304 / 300|152 / 150|qnrt, golden|10,067,934|100.68|
|clip0 0:640|640 / 640|320 / 320|qnrt, golden|19,851,462|198.51|

**6/6 windows, 20,822/20,822 logit bytes identical** (plus `cat1+2`,
128/120 frames, 1,856 bytes, in AUTO mode). The T300 count equals the engine
team's unstalled reference (10,067,933, docs/results/engine.md) exactly;
input starvation is 0 in all runs (cosim never stalls the streams). Cycles
are response-preparation clocks; they exclude response streaming, DMA setup,
cache maintenance and IRQ latency, which only the board can measure.

### Malformed packets, recovery, fallback (`--network-misc`)

|Case|Result|
|---|---|
|`model.qnc2_export.run_network` envelope sent raw|status 0, 64 + 464-byte response|
|allocated T_in = 641|status 4; next `engdrv_run_network` bit-exact|
|record 1 op = 99|status 4; next run bit-exact|
|record count 185|status 4; next run bit-exact|
|program truncated (early TLAST)|status 3; next run bit-exact|
|1 ms host deadline on a T640 RUN_NETWORK|ETIMEDOUT, engine/DMA reset, PING OK, next run bit-exact|
|`ENGDRV_NETWORK`, T_in = 656|E2BIG, no command sent|
|`ENGDRV_LAYER` on the same context|186 RUN_LAYER commands, bit-exact|
|`ENGDRV_AUTO` again|1 RUN_NETWORK, bit-exact|

`--misc` still passes on the new RTL (VERSION now 0x00010002; PING accepts
0x0001000x): 9/9 bad programs rejected, 4/4 malformed RUN_LAYER headers
return status 1/1/2/3 and recover, single-channel depthwise, async EBUSY/poll
(now a RUN_NETWORK), RUN_LAYER host timeout/reset.

### Per-layer fallback on the same RTL, including >800-frame tiling

|Window|T_in / valid|Commands|Logit bytes identical to|Engine cycles|
|---|---|---:|---|---:|
|clip2 full|32 / 18|186|qnrt, golden, manifest|1,839,818|
|clip0 0:304|304 / 300|186|qnrt, golden|10,885,695|
|clip0 full (AUTO: T_in > 640 falls back)|784 / 774|186|qnrt, golden, manifest|26,786,715|
|cat0+1|880 / 876|187 (C1 in 2 tiles)|qnrt, golden|29,967,774|
|cat0+0|1552 / 1548|188 (C1 in 3 tiles)|qnrt, golden|52,229,802|

5/5 windows, 51,504 logit bytes identical. With the S2b depthwise datapath
the per-layer 3 s window is 10.89 M cycles (depthwise 2.28 M, pointwise
5.84 M, add 2.77 M) versus 207.4 M on the S1/S2a RTL below.

### Host overhead per call

`engdrv_last_run` splits wall time into transport exchanges and work outside
them. Cosim host-side work outside the exchanges (x86, graph walk, encoding,
copies): RUN_NETWORK 0.007-0.097 ms per call; per-layer 13.9-45.4 ms.

Board, current stt-full (engine 0x00010001, so AUTO = per-layer; native
aarch64 `i2`, under `board.lock`; [`results/board_layer_overhead.log`](../../results/engdrv/board_layer_overhead.log)):

|Window|Runs|Wall ms|Engine prep ms|Wall - prep ms|Cache sync ms (calls)|Outside exchanges ms|
|---|---:|---|---:|---|---|---|
|304 / 300 (3 s)|5|2240.3-2250.4|2047.06|193-203|116.0-121.0 (744)|32.2-32.8|
|784 / 774|1|6105.4|5815.16|290|129.4 (744)|60.5|
|48 / 48|1|531.8|380.80|151|111.0 (744)|16.3|
|16 / 14|1|337.8|194.25|144|109.8 (744)|14.5|

All 8 runs PASS (logits and greedy CTC identical to qnrt). The 3 s window is
~40 ms faster than the I2 measurement (2284 ms): intermediate tensors are now
sized to their length instead of 800 frames. Per-layer mode costs about
0.16 ms per range sync and ~1 ms of host overhead per command.

RUN_NETWORK on the board replaces this with four range syncs (header+features
<= 41 KB, two SG descriptors in SG mode, response <= 9.3 KB) and one MMIO
DMA start; the program is cleaned once at load. From the per-sync cost above
the expected host overhead is about 1 ms per window, against ~195 ms in
per-layer mode. That is a projection: board RUN_NETWORK needs the timing-closed
0x00010002 bitstream and a >= 24 MiB u-dma-buf 2 (`program()` returns ENOSPC
with today's 8 MiB, which the AUTO fallback handles).

Tiled per-layer execution also ran on the board inside the I3 app runs:
finals of 868 and 846 valid frames (880/848 allocated) took 187 commands and
their logits equal `golden_int8.network` on the app's input bytes
(docs/results/app.md, I3).

## S1/S2a per-layer baseline (earlier run, superseded RTL)

Date 2026-11-03. Driver commit a7ff0b4 (tests) on engine RTL `HEAD`
3c666c0 (snapshot built with `make rtl-snapshot`; in-progress working-tree
edits to `rtl/engine/stt_add_ctrl.sv` were deliberately excluded). FedoraLinux-44
WSL, Verilator 5.046, repository `.venv/bin/python`. Cosim drives the real
`stt_engine` top through the same driver code as the board (`engdrv.c` encoder,
validator, network orchestration); only the transport differs.

## Build

```
make -C sw/engdrv rtl-snapshot
make -C sw/engdrv clean
make -C sw/engdrv cosim RTL=../../build/sw/engdrv/rtl/rtl/engine libqnrt.so all i2
```

Builds without warnings (Verilator `-Wall`, C `-Wall -Wextra`).

## Driver-level cosim results

Every command below was run from the repository root as
`.venv/bin/python sw/engdrv/tools/test_cosim.py ARGS`. Every run exited 0.

|ARGS|Result|
|---|---|
|`--misc --network clip2:full`|9/9 corrupted QNP2 programs rejected, prior program retained; 4/4 malformed packets return engine status 1/1/2/3 and the next PING succeeds; single-channel depthwise (groups=1) geometry correct; async second submit = EBUSY, poll returns logits; 1 ms host deadline on a 784-frame command -> ETIMEDOUT, engine/DMA reset, PING succeeds|
|`--vectors K/6`, K=0..5 (six parallel processes)|**558/558 manifest vectors bit-exact through `engdrv_run_layer`**: 282 pointwise, 231 depthwise, 45 add; 35,203,216 output bytes; output allocated and valid lengths checked|

Per-vector cycles are in
[`results/engdrv/manifest_vector_cycles.csv`](../../results/engdrv/manifest_vector_cycles.csv)
(vector-sum: pointwise 18,009,275, depthwise 652,115,123, add 16,424,082 cycles).

## Whole network on real windows (`engdrv_run_network`, per-layer RUN_LAYER)

Inputs are real quantized signed [64,T] C1 features saved in the vector
manifest (LibriSpeech dev-clean clips). Each window goes through the async
`engdrv_submit`/`engdrv_poll` path: 186 RUN_LAYER commands, intermediate
tensors round-tripped to host memory. Every logit byte, padding frames included,
plus output lengths, is compared with `qnrt_run_frames` (host build of
`sw/qnrt`) and `model/golden_int8.network`. Full clips are also compared with
the stored manifest logits.

|ARGS (`--network ...`)|Clip|T_in / valid|T_out / valid|Logit bytes identical to|Engine cycles|
|---|---|---|---|---|---:|
|`clip2:full`|121-127105-0003|32 / 18|16 / 9|qnrt, golden_int8, manifest|19,696,394|
|`clip0:0:48:48`|1089-134691-0023|48 / 48|24 / 24|qnrt, golden_int8|38,620,177|
|`clip1:full`|1188-133604-0025|112 / 102|56 / 51|qnrt, golden_int8, manifest|78,578,107|
|`clip0:0:300:300`|1089-134691-0023|300 / 300|150 / 150|qnrt, golden_int8|**207,409,338**|
|`clip0:0:304:300` (3 s window)|1089-134691-0023|304 / 300|152 / 150|qnrt, golden_int8|207,409,339|
|`clip0:full`|1089-134691-0023|784 / 774|392 / 387|qnrt, golden_int8, manifest|588,273,979|

## Per-layer-mode cycle baseline at T=150

This is the S1/S2a per-layer-mode baseline, before S2b's RUN_NETWORK. It comes from
`--network clip0:0:300:300`, recorded by `engdrv_network_timing`. Per-command counts:
[`results/engdrv/network_t150_cycles.csv`](../../results/engdrv/network_t150_cycles.csv)
(3 s window: [`network_3s_cycles.csv`](../../results/engdrv/network_3s_cycles.csv)).

|Kind|Commands|Cycles|Share|
|---|---:|---:|---:|
|Depthwise|77|196,096,396|94.5 %|
|Pointwise|94|5,838,248|2.8 %|
|Add|15|5,474,694|2.6 %|
|**Total**|**186**|**207,409,338**|100 %|

That is **2,074.09 ms at 100 MHz** and **1,382.73 ms at 150 MHz** of engine
preparation time per 3 s window. The largest commands are `b16.r0.dw`
(4,782,698) and the 15 B13 depthwise commands (4,168,266 each).

All 186 per-command counts equal the engine team's committed unstalled
references (`sim/engine/engine_t150_cycles.csv`, `depthwise_t150_reference.csv`,
`add_t150_reference.csv` at 3c666c0) exactly. Check:
`awk` join on layer name, result "186/186 layers exact".
The 3 s window (304 allocated input frames) differs only in C1, by +1 cycle.

Scope: these are response-header preparation counters. They exclude response
stream time, DMA setup, cache maintenance, IRQ latency and the 186 host round
trips that the per-layer mode costs on the board. The serialized 1x4-lane
depthwise datapath dominates, so this mode cannot meet the real-time target.
That is expected of the S1/S2a baseline and is what S2b (8x16 depthwise,
RUN_NETWORK, overlap) exists to fix. Cosim wall time (~1,460 s for the T=150
window) is simulator cost, not performance.

## Board transport build (aarch64)

Native build on the KV260 (Ubuntu 24.04, kernel 6.8.0-1036-xilinx, gcc 13.3.0)
from `git archive HEAD sw/engdrv sw/plio sw/qnrt sw/app` in `~/stt/engdrv`, via `tools/board.ps1`:

```
make CFLAGS="-O3 -g -std=c11 -fPIC -Wall -Wextra -Werror" all i2
```

Clean with `-Werror`. `libengdrv.a` (engdrv, board, plio, backend_pl, no_cosim
objects; 14 exported `engdrv_*` symbols) and the `i2` ELF aarch64 executable
were produced. `i2` with no arguments prints usage (exit 2). A probe calling
`engdrv_open` with `cosim=0` returns -1, errno 19 (ENODEV), because the
current overlay only has the frontend UIOs (`stt-dma-tx/rx`, `stt-frontend`,
...) and u-dma-buf 0/1. No board DMA, IRQ, cache or soak evidence exists until
the engine bitstream meeting the contract in [`docs/engdrv.md`](../engdrv.md) is
loaded.

## Open items

- Board execution: I2 (`tests/i2/i2.sh`), IRQ/cache/reset and soak
  results need the hardware integration build.
- Re-run the suite on the engine top once the pipelined add controller
  (uncommitted at the time of this run) is committed. Add cycles will then drop
  from 890*C to the new formula.
- App integration is done (`HAVE_ENGDRV`, docs/app.md).
- Board RUN_NETWORK, its DMA/IRQ/cache timing and the SG two-descriptor
  path need the 0x00010002 bitstream and a >= 24 MiB TX u-dma-buf.
