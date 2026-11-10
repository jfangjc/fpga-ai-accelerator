# Engine verification results

## S2b-4 depthwise area reduction (2026-11-04/05)

The 16-channel x 16-frame depthwise MACs use one DSP48E2 each. The first
product selects pipelined bias as feedback, matching the pointwise MAC
template. Signed 9x8 products and 33-bit prefix sums preserve UINT8/INT8
semantics and the deferred overflow check. Directed positive/negative tests
reject an overflowing prefix even when the next tap cancels it, and cover
final-tap overflow and safe near-boundary recovery. No MAC packing is used.
The DSP attribute is scoped to the arithmetic registers: a module-wide
attribute also moved controller address arithmetic into DSPs and was removed.

Sixteen 64x128-bit BRAM caches replace the three asynchronous full-row
gathers per channel. A synchronous read primes three aligned words and one
lookahead word in five clocks. Up to fifteen serial byte shifts align the
tile; tap issue then shifts by one/two bytes each clock, refilling at aligned
word crossings. Odd-address dilation-2 crossings insert the next word at
byte 31; other crossings insert it at byte 32. Each MAC receives one byte
per clock through fixed stride selections. The caches require 32 BRAM36s;
there is no additional URAM or replicated tensor bank.

### Area and timing

Synthesis hierarchy reports (LUT includes logic, LUTRAM and SRL):

|Checkpoint / scope|LUT|FF|DSP|RAMB36|RAMB18|URAM|
|---|---:|---:|---:|---:|---:|---:|
|f98b5c7 engine|106,229|54,595|583|18|2|60|
|f98b5c7 dw|71,859|22,497|0|0|0|0|
|DSP-only engine (`3c654ee`)|73,618|39,980|839|18|2|60|
|DSP-only dw|38,879|7,888|256|0|0|0|
|BRAM word-window engine (`5b0c9ca`)|50,484|43,030|839|50|2|60|
|BRAM word-window dw|18,103|10,922|256|32|0|0|
|Final byte-window engine|47,758|42,938|839|50|2|60|
|Final byte-window dw|15,363|10,835|256|32|0|0|

The final RTL is committed as `fd49011`, following working steps `3c654ee`
and `5b0c9ca`. The 100 MHz route closes with +0.751 ns setup and +0.010 ns
hold slack. The routed engine saves 58,367 LUTs (55.64%) against f98b5c7 and
meets the <=60k requirement. It remains 1,527 LUTs above the <=45k stretch
target. The independent 150 MHz route was run because 100 MHz exceeds the
required +0.5 ns setup margin; it fails setup at -1.138 ns, with 260 failing
endpoints and TNS -64.044 ns. Hold passes at +0.011 ns. The superseded word-window route was
stopped when the smaller byte-window implementation was ready; no routed
result is claimed for that intermediate checkpoint.

|Routed implementation|WNS (ns)|WHS (ns)|LUT|FF|DSP|RAMB36|RAMB18|URAM|
|---|---:|---:|---:|---:|---:|---:|---:|---:|
|f98b5c7, 100 MHz|+1.009|+0.011|104,894|54,602|583|18|2|60|
|Final, 100 MHz|+0.751|+0.010|46,527|42,944|839|50|2|60|
|Final, 150 MHz|-1.138|+0.011|46,372|42,280|839|50|2|60|

Report roots: `build/ooc_f98/100`, `build/s2b4_dsp_scoped`,
`build/s2b4_window_signed_100`, `build/s2b4_slide_100`, and `build/s2b4_slide_150`.
`synthesis_hierarchy.rpt` records the dw breakdown; `utilization.rpt`,
`timing.rpt`, `setup_paths.rpt`, and `hold_paths.rpt` are routed evidence.
All use Vivado 2026.1, xck26-sfvc784-2LV-c and eight threads. Jobs run detached
outside the sandbox using the normal per-user licence. The senior engineer's
`build/ooc_f98/150` run and its outputs were not modified.
The final dw synthesis breakdown is 15,048 logic LUTs, 296 LUTRAMs and 19
SRLs, compared with 64,440 logic LUTs, 7,400 LUTRAMs and 19 SRLs before.
The 100 MHz worst setup path is in the parser's descriptor geometry check,
from `parser/hw_reg[11][1]` to `parser/e_desc_reg`, not the depthwise MACs.
The same path is worst at 150 MHz (7.692 ns data-path delay, 26 logic levels).
The 150 MHz synthesis hierarchy reports 15,361 dw LUTs (15,046 logic, 296
LUTRAM, 19 SRL), 10,835 FFs, 256 DSPs and 32 BRAM36s; the engine synthesis
total remains 47,758 LUTs, 42,938 FFs and 839 DSPs. The supported physical
operating point established here is 100 MHz. Reaching 150 MHz needs further
timing work, including the parser geometry path; this checkpoint does not
claim 150 MHz closure.
These are OOC internal-clock results; top-level I/O timing and full-system
route/DMA-inclusive board performance are outside this measurement.

### Cycles and verification

|Preparation counter|Before S2b-4|Final|
|---|---:|---:|
|77 depthwise T150 RUN_LAYER commands|2,295,512|2,516,992|
|186 per-layer T150 commands|10,905,094|11,126,574|
|T300 input / T150 output RUN_NETWORK|10,087,333|10,308,813|

The final depthwise model's maximum relative error is 0.272586% (2% limit).
`depthwise_t150_reference.csv` is regenerated from simulation. The model
charges five BRAM priming clocks plus `(-padding) mod 16` alignment clocks
per frame tile. The depthwise aggregate remains below 3,000,000 cycles;
`check_network_cycles.py` now also enforces 10,500,000 cycles for the 3-second
network case. Counters exclude response DMA, cache/IRQ work and host overhead.
The before values above were remeasured on the DSP-only step with unchanged
scheduling; older sections below predate the existing extra tap-pipeline cycle.
The final 3-second network preparation count is 10,160,362 execution clocks,
85,994 weight-wait clocks and 62,457 reception/transition clocks. It takes
103.08813 ms at the timing-closed 100 MHz, excluding response DMA. The
mathematical 150 MHz conversion is 68.72542 ms, but 150 MHz does not close.

Final `make -C sim/engine check` passes (`sim/engine/s2b4_slide_check.log`):
eight Python exporter tests, 8,844 requantizer cases, 150 activation-unpacker
cases, 394 pointwise commands, 259 depthwise commands, 56 residual commands,
94 exact pointwise timing checks, 77 depthwise timing checks, 15 exact residual
timing checks, the 1024x1024x800 maximum command and all 12 network cases
(ten successful executions and two expected error responses). All 558/558
immutable manifest vectors remain bit-exact; fatal-warning lint passes.
The expanded 259-command depthwise set includes all 231 immutable depthwise
manifest vectors, 20 geometry/masking cases, six overflow/boundary cases and
malformed/recovery. All 77 additional depthwise T150 cases pass. Geometry
coverage includes 800-frame inputs, 87 taps, dilation 2, odd/even word
crossings, partial channel groups, signed/unsigned inputs and masked tails.


## S2b-2 encoder/allocation checkpoint (2026-11-03)

This checkpoint does **not** implement RUN_NETWORK in RTL. Opcode 2 still
returns unsupported-opcode status; no whole-network RTL logits or cycle
measurements are available. It must not be used as S2 acceptance evidence.

`model/test_qnc2_export.py`: 8 tests PASS, including the existing 558 manifest
command encodings, all 186 QNP2 record/array round trips, malformed program
framing/parameter checks, and new network envelope/liveness checks at
T_in/valid = 32/18, 48/48, 112/102, 300/300, 304/300, 320/320 and 320/0.
These new inputs are zero-byte feature payloads for format tests, not real
feature-window whole-network inference tests. Tests reject T_in=321, invalid
valid lengths and incorrect feature sizes. They check that output allocation
never aliases any live source and that tensors survive through final use.

Program size is 20,037,392 bytes. Candidate allocation: five 163,840-byte
banks, 30 URAM by width/depth geometry, peak three live banks for the current
serialized program; maximum 320 input/160 output frames. Full C3 storage fits.
This is allocation analysis, not synthesis or a hardware bank controller.

The unchanged per-layer preparation reference is 10,885,694 cycles:
pointwise 5,838,248, depthwise 2,276,112, add 2,771,334. This equals
108.85694 ms at 100 MHz or 72.57129 ms at 150 MHz, excluding response DMA,
driver/cache/IRQ work and host round trips. It includes per-layer parsing and
input loading, so it is not a pure compute counter or a RUN_NETWORK prediction.
Network compute/weight-stream-stall/layer-transition breakdown is unavailable
until scheduler RTL and counter instrumentation exist. At 1.6 GB/s the program
alone takes 12.52337 ms to transfer; successful overlap still needs measurement.

Regression rerun in FedoraLinux-44 with Verilator 5.046:
`make -C sim/engine check` completed with `PASS engine check`. This includes
full-top lint, 8,844 requant cases, 150 unpack cases, 394 pointwise cases,
add and depthwise suites, 94 exact pointwise T150 cycle checks, 77 depthwise
cycle checks (maximum error 0.285948%), and the 1024x1024x800 maximum case.
The expanded eight-test Python exporter suite also passed independently.

Required next implementation: replace the oversized activation banks, add
checked QNP2 dispatch and tensor retirement, route on-chip sources to existing
controllers, preserve weights' two-tile prefetch, and emit only final logits.
Then run golden whole-network windows, malformed/truncated/recovery and random
AXIS-stall cases. S2b-3 overlap/clock/OOC work must follow that functional
checkpoint; no new Vivado run was started here.

S1 is **accepted** (2026-11-03): full regression PASS (re-run by the senior engineer at
1bad1ba) and 100 MHz post-route timing met (WNS +2.970 ns).
Tests exercise the real `stt_engine` top, not a datapath-only wrapper.

## Reproduction

In FedoraLinux-44 from the repository root:

```
make -C sim/engine check
```

Verilator 5.046 and `.venv/bin/python` are used. The Makefile propagates errors
and prints PASS/FAIL. Golden outputs are immutable files from the vector
manifest; the testbench does not recompute its oracle using RTL algorithms.
The standalone activation test uses narrowly scoped file/message waivers for
unused shared package constants and configuration fields. Full-top `-Wall`
lint passes with only source-local unused-field/unused-constant annotations.

|Check|Measured result|
|---|---|
|QNW1/QNP2/S1 encoder Python tests|3 PASS, S1 encoding checked for all 94 records|
|Eight-stage requantizer, random valid gaps/backpressure|8,844 PASS|
|Activation unpacking/masking, including 1024x800|150 cases, 893,408 checked bytes PASS|
|Full-top pointwise manifest vectors|282 / 282 PASS|
|Full-top extra T=150 golden prefixes|94 / 94 PASS|
|Full-top valid synthetic layers|16 / 16 PASS|
|Full-top intermediate-overflow cancellation cases|2 / 2 PASS, status 5|
|Full-top total layer cases/output bytes|394 / 23,759,470 PASS|
|Unstalled T=150 cycle-model comparison|94 / 94 exact, zero cycle difference|
|Expanded protocol/register/IRQ recovery suite|13 / 13 PASS|
|Maximum Ci=Co=1024, T=800 streamed command|819,200 bytes PASS; 1,774,451 transaction clocks|

The full-top randomized regression was run with deterministic source gaps and
sink backpressure on complete commands. It includes all residual projections,
C3 512->1024 and C4 1024->29, signed/unsigned inputs, ReLU/non-ReLU outputs,
T=1/17/31/7, Ci=1/19/17/3, Co=1/29/33, short/zero valid lengths, and consecutive
commands. Output data/TLAST stability is checked while stalled.
The separate unpacker regression sweeps every T from 1 through 49 with full,
half and zero valid lengths plus maximum dimensions.

## Full-engine T=150 cycle measurements

`sim/engine/engine_t150_cycles.csv` records all 94 layer names, shapes, total
transaction clocks and response-header preparation clocks. The latter exclude
response DMA but include command input, activation realignment, parameter RAM
load, initial weight fill, compute, drain and requantization.
`engine_model.s1_t150_cycles` matches every measured preparation count:

|Ci|Co|Measured preparation clocks|Model|Difference|
|---:|---:|---:|---:|---:|
|64|256|6,155|6,155|0|
|256|256|23,819|23,819|0|
|256|512|44,499|44,499|0|
|512|512|88,531|88,531|0|
|512|1024|170,851|170,851|0|
|1024|29|22,617|22,617|0|

Sum over all 94 layers: 5,838,248 preparation clocks (58.38248 ms at 100 MHz),
excluding response DMA and PS depthwise processing. This is not whole-network
latency or measured CPU speedup. The original provisional S2 model remains
separate; it does not establish implemented S2 performance.

## Physical characterization

Command (Windows normal user session, per-user licence):

```
C:\AMDDesignTools\2026.1\Vivado\bin\vivado.bat -mode batch -source sim/engine/ooc_engine.tcl -log sim/engine/ooc_engine.log -journal sim/engine/ooc_engine.jou
```

The script reads its clock XDC before synthesis, sets HD.CLK_SRC, uses eight
threads and performs independent opt/place/route runs from one unplaced synthesis checkpoint, one target clock at a time.
The sweep stops on the first negative WNS. Initial runs were stopped after correcting intermediate accumulator overflow
and finding split multiply/add DSP inference. A one-cell synthesis probe
(`mac_probe.tcl`) confirms that matching the registered product width to the
33-bit accumulator produces exactly one DSP48E2, with `(C or P)+A*B` and
A/B, M and P registers enabled. The full-engine build must establish the
final array-wide count; the probe alone is not physical acceptance.

|MHz|Post-route WNS|LUT|FF|DSP|BRAM36|URAM|
|---:|---|---|---|---|---|---|
|100|+2.970 ns (WHS +0.010 ns)|22,566 (19.27 %)|29,003|578|19|52|
|150|Not completed (Vivado exited after synthesis checkpoint; superseded by S2a builds)|-|-|-|-|-|
|175|Not run|-|-|-|-|-|

Full-engine synthesis completed: 22,999 LUT (19.64%), 29,002 FF, 578 DSP,
19 BRAM36 equivalents and 52 URAM. The DSP count is 512 array MACs,
64 requantizer DSPs and two control multipliers. See
`sim/engine/reports/engine_synthesis100.rpt`. Post-route at 100 MHz: `build/sim/engine/ooc_engine_100/{timing,utilization}.rpt`. The 100 MHz slack implies roughly 142 MHz achievable for the S1 top; independent 150/175 MHz implementations move to the S2a/S2b builds, since those change the engine.

## Remaining milestones

S1 physical acceptance requires 100 MHz WNS>=0 and confirmation of 512 array
DSP MACs; characterize 150/175 MHz with independent implementations as stated.
The required unified `make -C sim/engine check` run passed (exit 0) again after the one-DSP product-width fix, including all 394 golden/synthetic cases, 13 recovery/register/IRQ cases, all 94 exact cycle comparisons and the maximum-size command.
S2a adds depthwise, scaled residual addition and interlayer mask/bank control.
S2b adds RUN_NETWORK, QNP2 scheduling, residual tensor lifetime management,
C3/C4 storage/fusion handling and whole-network throughput measurements.

## S2a planning checkpoint (2026-11-03)

Only command encoding and a proposed datapath sizing model are implemented
at this checkpoint. No S2a RTL or S2a OOC result is claimed.
`model/test_qnc2_export.py` checks payloads for all 558 manifest commands;
this is transport encoding coverage, not hardware execution coverage.

`engine_model.s2_overlap_estimate(read_qnw(blob), steps=150, mhz=...)`
models a 300-input-frame window and 150 post-C1 frames. It uses the measured
S1 pointwise preparation model, proposed 128-MAC depthwise cycles and two
passes through the shared requantizers for add. Per-layer proposed counts
are returned in `rows`; they are not compared to S2a RTL yet.

|Clock|Serialized estimate|Optimistic DW/PW overlap bound|
|---:|---:|---:|
|100 MHz|8,668,456 cycles / 86.685 ms|5,961,368 cycles / 59.614 ms|
|150 MHz|8,668,456 cycles / 57.790 ms|5,961,368 cycles / 39.742 ms|

These estimates exclude response transport, software/cache costs and some
pipeline startup. They retain S1 pointwise per-command load overhead even
though S2b will use a network command. The overlap bound assumes independent
ports and ignores shared requantizer contention. It is neither a measured
speedup nor proof of meeting the 109/72 ms end-to-end targets.

Open implementation work: depthwise controller/window/weight access, add
branch storage and INT16 requantization, parser and response length changes,
all 558 hardware vectors and synthetic/protocol cases, exact S2a cycle model,
and independent 100/150 MHz OOC runs. The S1 OOC table above is historical
and cannot establish S2a resources or timing.

Checkpoint validation: `make -C sim/engine check` completed with exit 0 after
the encoder changes: 8,844 requant cases, 150 activation cases (893,408
bytes), 394 full-engine layers (23,759,470 bytes), 94 T150 comparisons with
zero cycle difference, and the 1024x1024x800 command (819,200 bytes).
The final Python suite has six passing tests, including the sizing-model
checks. No RTL was changed in these two implementation checkpoints.

## Small depthwise datapath: functional checkpoint

The full `stt_engine` top passes all **231 depthwise manifest vectors**
bit-exact against the stored oracles. An additional **10 synthetic cases**
cover signed/unsigned input, T=1/17/19/31, partial and zero valid lengths,
odd stride-2 input lengths, 87 taps with dilation 2, and both-end padding.
A malformed depthwise command returns status 4, followed by successful
recovery in the same engine instance (243 depthwise commands total).
The transaction driver injects TVALID gaps and TREADY backpressure and
checks stalled response stability. Eight independent full-top simulators
run contiguous command groups; the malformed/recovery pair stays together.

The tested implementation has **one channel lane x four frame lanes**.
`DW_FRAME_LANES` parameterizes frame MAC count (1/2/4/8/16); only the default
four is claimed tested. A channel cache loads via the S1 activation port,
then the controller iterates tap weights and frame groups. The existing
weight ping-pong RAM, parameter RAM, Q31 requantizers, output RAM and response
packer are reused. Input and output lengths are distinct; signed arithmetic
shift implements floor division for zero-valid stride-2 commands.

No Vivado runs were performed. Existing S1 implementation reports remain
historical; the added controller/cache and pointwise operand enables have
no new synthesis/resource/STA evidence. Add, RUN_NETWORK and bank ownership
are unimplemented. Scaling to 8x16 needs eight independently addressed read
banks/channel caches, channel-parallel control and serialized row draining.

### Complete regression and measured schedule

`make -C sim/engine check` completed with exit 0 in FedoraLinux-44,
Verilator 5.046, `-Wall` fatal, using the repository `.venv/bin/python`.
The complete acceptance target includes:

| Check | Passed |
|---|---:|
| Exporter/model Python tests | 6 |
| Requantization with gaps/backpressure | 8,844 |
| Activation unpack | 150 (893,408 bytes) |
| Pointwise full top | 394 (23,759,470 output bytes) |
| Depthwise manifest full top | 231 |
| Depthwise synthetic full top | 10 |
| Malformed depthwise and same-instance recovery | 2 |
| Depthwise unstalled T150 | 77 |
| Pointwise unstalled T150, exact cycle matches | 94 |
| Maximum pointwise 1024x1024x800 | 1 (819,200 output bytes) |

All 77 depthwise timing cases have **150 output frames**; stride-2 C1
receives 300 input frames. Measured response-preparation counters range from
**287,468 to 4,782,698 cycles**. `engine_model.depthwise_cycles` is **14 cycles
high for every layer**, maximum relative difference **0.004870%**, within
its documented 2% tolerance. The model explicitly charges input realignment,
parameter load, first weight fill, cache reads, weight fetches, frame-group
MACs and row sequencing. These counters exclude response DMA and injected
stalls. The complete reproducible layer table is committed as
[`depthwise_t150_reference.csv`](../../sim/engine/depthwise_t150_reference.csv).
The S1 model continues to match all 94 measured counters exactly.

Files changed for this work:

- RTL: `stt_dw_ctrl.sv`, `stt_cmd_parser.sv`, `stt_engine_pkg.sv`,
  `stt_engine.sv`, and operand enables in `stt_mac_array.sv`.
- Simulation: `depthwise_vectors.py`, `test_depthwise.py`,
  `check_depthwise_cycles.py`, `depthwise_t150_reference.csv`, `tb_engine.cpp`,
  `test_engine.sh`, `Makefile`, and `.gitignore` under `sim/engine/`.
- Model/documentation: `model/engine_model.py`, `docs/engine.md`,
  `docs/qnc2_protocol.md`, and this results file.

Residual add still needs its second input store, two independent Q31-to-INT16
branch conversions, INT32 sum/UINT8 clamp, parser/dispatch and full-top tests.
The small depthwise controller does not establish S2b overlap, eight-channel
bandwidth, resource fit or clock closure.
## S2a-2 residual implementation

The updated top implements pointwise, depthwise and residual RUN_LAYER.
The manifest splits into 282 pointwise, 231 depthwise and 45 add vectors
(558 total). Residual conversion uses separate Q31 products and one
half-away rounding per branch, clamps each branch to INT16, sums in INT32,
and clamps once to UINT8. It preserves allocated samples and valid length.
A scalar multiplier is shared across lanes/branches. Existing activation
banks hold the two branches; each second-branch word is overwritten only
after both reads complete. No extra full-sized activation bank is added.

Verified on this implementation so far: all 45 add manifest vectors, nine
synthetic cases (T=1/17/31, zero/partial/full valid, extreme positive Q31
multipliers, shifts -30 through INT32_MAX, both INT16 and UINT8 saturation),
and malformed-command/same-instance recovery. The driver injects TVALID
gaps and TREADY backpressure and checks response stability. All 15 unstalled
T150 add layers match the cycle model exactly: C=256 takes 228,122 cycles;
C=512 takes 456,218. Formula: 890*C + 4*ceil(C/4) + 26, excluding response
DMA. `sim/engine/add_t150_reference.csv` records the complete layer table.
The existing 394-command pointwise suite passes (23,759,470 output bytes),
and explicit full-top `verilator --lint-only -Wall` passes.

The complete acceptance target is running in `sim/engine/s2a2_check.log`.
Its final status will be recorded below. Historical depthwise and S1 OOC
measurements above must not be treated as updated-top acceptance results.

The independent 100/150 MHz implementations run in a detached, hidden
Vivado process, with eight threads, HD.CLK_SRC, and separate opt/place/route
from an unplaced synthesized checkpoint. The sweep completes 150 MHz even
if 100 MHz misses timing. Live log: `sim/engine/s2a2_vivado.log`; report
folders: `build/sim/engine/ooc_engine_100` and `ooc_engine_150`. Old reports in
those folders are excluded until their timestamps postdate this launch.

| Updated top target | WNS | WHS | LUT | FF | DSP | BRAM36 | URAM |
|---|---:|---:|---:|---:|---:|---:|---:|
| 100 MHz | pending | pending | pending | pending | pending | pending | pending |
| 150 MHz | pending | pending | pending | pending | pending | pending | pending |

S2b must implement RUN_NETWORK whole-program execution with on-chip tensor
liveness; scale depthwise to about 8 channels x16 frames using independently
addressed activation banks; implement depthwise/pointwise overlap and shared
resource scheduling; and establish a routed clock target and measured
end-to-end throughput. The current serialized datapaths establish arithmetic
coverage, not that overlap or the whole-network performance target.

### Updated-top manifest coverage

All eight depthwise shards have completed successfully on the updated top:
231 depthwise manifest vectors plus ten synthetic and malformed/recovery
commands (243 total). Together with 282 pointwise and 45 residual manifest
vectors, **558/558 manifest vectors pass bit-exact through stt_engine**.
The complete acceptance target continues with the 77 depthwise T150 timing
cases, 94 pointwise timing cases and maximum-size command; full-target exit
status is recorded separately when it completes.

### Complete S2a-2 acceptance run

`make -C sim/engine check` completed with exit 0 on the updated residual top:
6 exporter/model tests, 8,844 requantizer cases, 150 unpacker cases,
394 pointwise commands, 243 depthwise commands, 56 residual commands,
77 depthwise T150 comparisons (maximum error 0.004870%), 94 exact pointwise
T150 matches, 15 exact residual T150 matches, and the 1024x1024x800 command
(819,200 output bytes). Manifest coverage is **558/558**.

Initial synthesis exposed a multidimensional add parameter memory expanded
into 131,072 registers (about 162k total FF). That run was stopped before
implementation. Parameter storage was changed to four explicit distributed
128-bit RAM banks, with the same asynchronous read and cycle schedule.
The full-top add suite and its timing comparisons are rerun for this storage
change. OOC was restarted; only this restarted run's reports are accepted.
`collect_ooc.ps1` waits for Vivado and fills the pending OOC table above from
fresh routed reports. The initial register-heavy synthesis is not an
accepted resource result.

The explicit RAM version passes all 56 add commands and all 15 exact T150 cycle comparisons; full-top fatal-warning lint is clean. Convolution datapaths are unchanged by this parameter-store-only refinement.

### Fresh synthesis after explicit parameter RAM inference

Vivado 2026.1 synthesis completed with zero errors and zero critical warnings:
32,727 LUT, 31,075 FF, 586 DSP, 19 BRAM36, 52 URAM. This is synthesis
utilization, not routed timing evidence. It replaces the rejected register-
expanded diagnostic run. The updated top remains within the LUT/DSP
engineering limits and device memory capacities at synthesis. Independent
100/150 MHz opt/place/route implementations are in progress.

## S2b-1 parallel depthwise (2026-11-03)

Implementation commits: `fa6e7eb` (8x16 pipeline) and `4038bb1` (16x16).
The first configuration passed depthwise arithmetic but cost 3,584,212
T150 cycles, exceeding the 3M budget. The chosen **16 channels x16 frames**
uses 256 MACs and takes **2,276,112 cycles** over all 77 depthwise layers:
86.15x faster than the supplied 196,096,396-cycle S2a baseline. The extra
128 multipliers versus 8x16 are justified by the measured cycle budget;
expected arithmetic DSP demand is about 834 (baseline 586 minus four old
DW lanes plus 256), below 1,100. This is not a synthesis resource claim.

Each group preloads sixteen independent full-channel activation caches and
sixteen weights per tap through pipelined existing RAM ports. Gather,
multiply and accumulate are registered; one 256-MAC tap issues per clock.
Caches are reused over every frame tile, then sixteen rows drain through
the shared requantizer. No replicated full tensor bank, operand packing,
or depthwise/pointwise concurrency is implemented. The large cache gather
mux still requires physical validation. See `engine.md` for architecture.

T150 response-preparation counts (response transfer and stream stalls excluded):

| Kind | Layers | Cycles |
|---|---:|---:|
| Depthwise | 77 | 2,276,112 |
| Pointwise | 94 | 5,838,248 |
| Pipelined residual add | 15 | 2,771,334 |
| Total | 186 | 10,885,694 |

The per-layer depthwise measurements and model predictions are checked in
`sim/engine/depthwise_t150_reference.csv`. All 77 predictions agree within
0.285948% (2% limit); the checker also enforces a 3,000,000-cycle total.
The core sum corresponds to 108.85694 ms at 100 MHz or 72.57129 ms at
150 MHz. Neither proves the DMA-inclusive 109/72 ms target. Host round
trips and DMA add overhead, and the 150 MHz core alone exceeds 72 ms.

Validation: 243 depthwise commands pass (231 immutable manifest oracles,
ten signed/unsigned geometry/masking cases, malformed/recovery), plus all
77 T150 cases. Synthetic channels now include 3, 9, 31, and 33 to cover
partial lane groups and the weight tile boundary. Fatal-warning lint passes.
Full `make -C sim/engine check` result is recorded below when complete.

The senior engineer's Vivado run is still active; its output directories
and log were not modified. No updated-top OOC run has started, and existing
reports must not be attributed to these commits.

| Updated 16x16 top | WNS | WHS | LUT | FF | DSP | BRAM36 | URAM |
|---|---:|---:|---:|---:|---:|---:|---:|
| 100 MHz | pending | pending | pending | pending | pending | pending | pending |
| 150 MHz | pending | pending | pending | pending | pending | pending | pending |

Remaining acceptance: after that Vivado process exits, run
`sim/engine/ooc_engine.tcl` outside the sandbox for independent 100/150 MHz
route results; inspect utilization and WNS/WHS and fix any overuse/timing
failure. S2b also needs RUN_NETWORK with on-chip tensor liveness to remove
186 host round trips, bank/port scheduling and intermediate storage for
depthwise/pointwise overlap, and a measured board clock/end-to-end result.

### Complete S2b-1 simulation acceptance

`make -C sim/engine check` completed successfully on the final 16x16 top
(log: `sim/engine/s2b_check16.log`): 6 exporter tests, 8,844 requantizer
cases, 150 activation-unpacker cases, 394 pointwise commands, 243 depthwise
commands, 56 residual commands, 77 depthwise T150 cases (maximum model
error 0.285948%), 94 exact pointwise T150 matches, 15 exact residual T150
matches, and the 1024x1024x800 maximum-size command (819,200 output bytes).
All **558/558 manifest vectors** remain bit-exact. Fatal-warning lint passes.
At handoff the senior Vivado run remains active in optimization, so updated
16x16 physical acceptance is blocked; no timing/resource result is claimed.

## RUN_NETWORK serialized checkpoint (2026-11-03)

Engine version 0x00010002 executes the fixed 186-record QNP2 graph in stream
order, retains interlayer tensors in a shared activation pool, and returns one
signed logits tensor. The pool is ten 160 KiB slices, mapped as two 800 KiB
RUN_LAYER regions or five 320 KiB RUN_NETWORK regions. Nominal storage mapping
is 60 URAM; no Vivado job was run for this checkpoint. RUN_LAYER retains its
1024x800 maximum. Network limits are 640 input / 320 output frames.

The T32/valid18 whole-top result is bit-exact both to the stored manifest and
to `golden_int8.network`; this test injects random TVALID gaps and TREADY
backpressure. Larger fixtures use the same independent whole-network oracle.

Unstalled T300 input / T150 output preparation measurement:

|Counter|Clocks|ms at 100 MHz|ms at 150 MHz|
|---|---:|---:|---:|
|Layer execution excluding weight waits|9,919,482|99.19482|66.12988|
|Controller waits for unavailable weights|85,994|0.85994|0.57329|
|Reception, record checks and transitions|62,457|0.62457|0.41638|
|Total preparation|10,067,933|100.67933|67.11955|

Preparation ends before response DMA; the counters sum exactly to the response
header cycle count. Execution includes controller/cache reads, arithmetic,
drain and requantization. Reception/transitions includes input features,
program headers, parameter arrays and layer startup. Weight beat reception can
overlap execution inside each layer; there is no cross-layer prefetch.
`engine_model.serialized_network_cycles(records, 300)` predicts 10,069,481
cycles (+0.0154%). The model is approximate; the counter sum is exact.

S2b-3 work remains cross-layer weight/parameter overlap, physical inference
and timing confirmation, and hardware end-to-end DMA measurement. The current
fixed descriptor table must be regenerated and revalidated if topology changes;
it intentionally rejects alternate QNP2 graphs. Detailed timing counters are
simulation-visible and do not extend the four-beat response ABI.

Whole-top network coverage: one independent golden comparison at each input
length 32 (valid 18), 48, 112 (valid 102), 150, 300, 304 (valid 300), 320 and
640. T32 additionally runs twice after malformed/truncated commands, for ten
successful full-network executions plus two expected error responses (12 cases).
The three T32 runs inject deterministic random source gaps and sink backpressure.
T640 produces the full allocated [29,320] logits and exercises C3's complete
[1024,320] tensor across both physical slices of its network region.

Existing regression evidence: all 558 stored layer vectors remain bit-exact;
394 pointwise/full-top commands, 243 depthwise commands, 56 add commands,
94 exact pointwise timing comparisons, 77 depthwise timing comparisons,
15 add timing comparisons, and the original 1024x1024x800 maximum case pass.
The suite also retains 8,844 requantizer and 150 unpacker cases and eight Python
exporter tests. Network fixtures and counter/model checks are included in
`make -C sim/engine check`.

## S2b-3/4/5 timing closure and area (2026-11-04/05, consolidated by the senior engineer)

Fixes, in order: depthwise gather restructuring and incremental addressing
(ee4e291, deee4f5); activation-pool address decode without a general divider
and a counter for the residual-add address (f98b5c7); depthwise MACs on
DSP48E2 with BRAM caches and sliding windows (S2b-4, b1cb4f5); parser
descriptor geometry pipelined over four record-start cycles (ff9a337);
accumulator-drain capture strobe replicated (945c628, then `max_fanout`
replication instead of a kept per-cell register, which blocked LUT combining).

Post-route, engine out-of-context (`sim/engine/ooc_engine.tcl`, one independent
synthesis + implementation per clock; reports under `build/`):

| Revision | MHz | WNS ns | WHS ns | Failing | LUT | FF | DSP | URAM |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| f98b5c7 (addr fix, LUT depthwise) | 100 | +1.009 | +0.011 | 0 | 104,894 | 54,602 | 583 | 60 |
| f98b5c7 | 150 | -1.468 | +0.011 | 397 | - | - | - | 60 |
| b1cb4f5 (S2b-4, DSP depthwise) | 100 | +0.751 | +0.010 | 0 | 46,527 | 42,944 | 839 | 60 |
| b1cb4f5 | 150 | -1.138 | +0.011 | - | 46,372 | 42,280 | 839 | 60 |
| 945c628 (parser + per-cell drain strobe) | 150 | **+0.312** | +0.011 | 0 | 54,101 | 42,887 | 836 | 60 |
| 945c628 | 100 | +2.157 | +0.010 | 0 | 54,645 | 43,581 | 836 | 60 |
| max_fanout drain strobe (this commit) | 150 | **+0.292** | +0.010 | 0 | **46,348** | 42,600 | 836 | 60 |
| max_fanout drain strobe (5d7613a) | 100 | +1.181 | +0.010 | 0 | 46,718 | - | 836 | 60 |

Regression (`make -C sim/engine check`) re-run by the senior engineer on 945c628
and on the max_fanout working tree: PASS — 558/558 golden vectors, 259
depthwise commands, 12 RUN_NETWORK windows, all cycle models (depthwise T150
2,517,300 cycles; pointwise and residual exact).

Result: **the engine closes 150 MHz** (and 100 MHz). A 3 s window (T_in 300,
T_out 150) is 10.31M cycles = 103.1 ms at 100 MHz and **68.7 ms at 150 MHz**,
compute only, i.e. about 2.1x and 3.2x faster than the 217 ms CPU INT8
baseline before DMA/driver overhead (measured on the board in T1).
