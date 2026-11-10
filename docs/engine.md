# Engine architecture

## Serialized RUN_NETWORK (engine version 0x00010002)

Opcode 2 executes the unchanged 186-record QNP2 stream on the existing
pointwise, depthwise and residual controllers. Features enter once; layer
results remain on chip, and only the final signed logits are returned.
Descriptors are checked against the fixed exported graph before execution.
`sim/engine/generate_network_table.py` regenerates the descriptor and routing
functions from the exported program and host liveness planner. Parameters and
weights remain streamed data; the table contains no trained arrays.

One activation pool contains ten independently addressed 128-bit by 10,240-word
slices (1,638,400 bytes). RUN_LAYER maps two regions of five slices, preserving
1024 channels at 800 frames. RUN_NETWORK maps five regions of two slices,
allowing 1024 channels at 320 output frames and 64 channels at 640 input frames.
Each slice infers six URAM blocks, totaling 60 in Vivado OOC. There are no duplicate legacy
activation memories. Each read selector follows the four-cycle RAM latency.
Disjoint source/output regions permit concurrent read and write; residual add
reads its two source regions independently. The current serialized graph peaks
at three live regions. C3 fits in full without fusion.

Every exact descriptor check protects the precomputed bank retirement schedule.
Per-region allocated/valid lengths and signedness propagate at layer completion.
Pointwise and depthwise mask invalid source positions at each read boundary;
add preserves allocated positions. No intermediate tensor is emitted. Each
layer resets controllers, parameter error state and weight-cache ownership;
activation RAM persists. Next-record streaming begins after the previous layer
finishes; weight tile loading overlaps computation inside each convolution.
Cross-layer prefetch remains S2b-3 work.

Malformed records drain to packet TLAST, and truncation without TLAST uses the
existing idle watchdog. Arithmetic errors terminate the network and propagate
to its single response. See qnc2_protocol.md for the DMA/cache contract.

`stt_engine` implements individual layers and serialized full networks on a single `aclk`.
The control port is AXI4-Lite; both DMA streams are 128-bit AXI4-Stream with
TKEEP and packet TLAST. Reset is synchronous active-low `aresetn`. See
[qnc2_protocol.md](qnc2_protocol.md) for the complete wire and register maps.
S1 acceptance still requires the full regression and post-route results in
[results/engine.md](results/engine.md); compilation alone is not acceptance.

## Data path

The parser consumes a four-beat command header, channel-major activations,
one QLR2 descriptor, bias/multiplier/shift arrays, then tiled weights.
Input precedes weights because a bounded two-tile weight cache cannot retain
a complete layer while waiting for later input. Parameters precede weights.
`qnc2_export.run_layer` emits this S1 envelope. RUN_NETWORK streams features followed by a QNP2 program.

`stt_act_unpack` realigns arbitrary row lengths into sixteen-frame words.
Input positions >= valid length and unused final lanes are zeroed in hardware.
The RUN_LAYER address map exposes 51,200 words per region, addressed as
channel*ceil(T/16)+frame_tile, with four-cycle read latency. Separate input
and output banks use inferred UltraRAM. RUN_LAYER supports Ci/Co <=1024 and T<=800; network regions use the shared pool described above.

The weight cache comprises two halves, each holding 1024 rows of 32 weights.
Two 128-bit stream beats fill each row. Full/available flags prevent overwrite;
the controller releases a half only after issuing all frame tiles for that
output tile. The writer can fill the alternate half while computation runs.
Weights are loaded once per output tile and reused over every frame tile.
The current cache uses inferred block RAM; the main activation storage uses
UltraRAM. Parameters occupy three 128-bit-wide RAMs with two-cycle reads.
A separate set of 32 small distributed bias banks supplies the array's
initial feedback operands in parallel.

`stt_pw_ctrl` broadcasts one reduction term per cycle to a 32-output by
16-frame output-stationary array. Input signedness selects sign extension of
INT8 or zero extension of UINT8 to nine bits. A/B, product and accumulating
P registers are pipelined; first/last controls and tags follow the operands.
The first term selects bias as feedback. Each cell retains 33 signed bits,
which bounds every legal 1024-term reduction including bias. Overflow checks
inspect every prefix against INT32 bounds and latch errors even if later
terms cancel. Overflowing commands emit no successful output.
The intended mapping is one DSP48E2 multiply-and-accumulate per cell (512);
only a utilization report establishes the actual mapping.

Finished tiles are captured into `stt_acc_drain`, allowing the array to start
the next frame tile while 32 rows drain. Reductions shorter than 32 terms are
spaced to prevent capture collisions. Sixteen eight-stage elastic
`stt_requant` pipelines implement the exact Q31 contract: one wide multiply,
half-away rounding, sign restoration and signed/unsigned clamp. No bias is
added after reduction. Addresses follow matching pipeline delays. Results
are retained in the output bank until the final command status is known.

The response packer reads that bank through a credit-limited FIFO and merges
partial rows back into contiguous channel-major output bytes. Data and TLAST
remain stable under backpressure. The four-beat response header includes tag,
status, preparation cycles, input-starvation cycles and issued terms. The
final beat carries TLAST. Error/PING responses have no output payload.

## Control and recovery

The opcode dispatch accepts PING, pointwise/depthwise/add RUN_LAYER, and RUN_NETWORK; other opcodes
return status 2. Invalid limits/records/parameters return status 4. Length,
TKEEP and early/missing TLAST errors return status 3. Parser errors drain to
TLAST; the idle watchdog handles streams that stop without TLAST and pauses
when the receiver applies backpressure. A response-completion cleanup cycle
resets datapath state before the next command. Soft reset aborts all state;
software must reset both DMA channels too.

AXI-Lite address and data writes are buffered independently. IRQ is level:
completion/error status latches on the final response handshake, enable bits
select sources, and W1C acknowledges. Software-visible cycle counters include
response transmission; response-header cycles end at response preparation.
These are deliberately distinct measurements.

## Verification and scheduling

`make -C sim/engine check` runs Python exporter tests, 8,844 requantizer cases,
150 independent activation-unpacker cases, full-top streamed golden and
synthetic commands, and the unstalled T=150 cycle check. Tests return nonzero
on failure. `engine_t150_cycles.csv` contains all 94 pointwise layers.
`engine_model.s1_t150_cycles` includes actual input realignment, parameter
load, initial weight fill, controller, drain and requantizer overhead; all
94 measured response-header counters match exactly. Response DMA and injected
stalls are excluded from this closed-form comparison. The older `predict`
function remains a provisional S2 whole-network estimate and is not S1 timing.
The old `tb_array*` tests targeted a superseded MAC interface and have been
removed (see git history); full-top tests replace them.

`ooc_engine.tcl` reads a clock XDC before synthesis, sets HD.CLK_SRC and uses
eight Vivado threads. It runs separate synthesis/opt/place/route implementations
at 100 and 150 MHz, completing both targets even if 100 MHz misses timing. Optional
Tcl arguments select other target frequencies; `STT_OOC_ROOT` isolates report and
checkpoint directories from other builds. Detailed setup and hold path reports
are emitted alongside each timing summary. It does
not report retimed STA on one routed checkpoint as separate implementations.

## Historical checkpoints

The sections below record earlier implementation stages. The current capability
and memory limits are defined by the RUN_NETWORK section above.

### Original milestone plan

S2a adds a depthwise datapath, exact scaled residual addition, bank ownership
and masking at each interlayer boundary. S2b adds RUN_NETWORK/QNP2 dispatch,
tensor lifetime management, C3/C4 storage/fusion scheduling and end-to-end
performance measurements. The current full 1024-channel input/output banks
consume substantial memory; S2 must explicitly solve residual bank liveness
within 64 URAM and 144 BRAM36 rather than assume additional full-sized banks
will fit. Final resource limits remain <=70% LUT and <=1100 DSP.

## Historical S2a planning checkpoint (encoder and model only)

The RTL above remains S1. The proposed wire extension is documented in
qnc2_protocol.md and encoded by model/qnc2_export.py; it is not accepted by
the current parser. All 558 manifest commands are checked for byte layout.
C4 logits already execute on S1 as a signed-output pointwise layer.

`engine_model.s2_overlap_estimate` sizes the proposed depthwise datapath at
8 channels x 16 frames = 128 MACs. Eight independently addressed 128-bit read
banks deliver 128 bytes/clock to a 4 KiB window cache (32 words/channel lane).
The maximum 16-output receptive span is 203 samples at stride=2, dilation=2,
K=87, requiring at most 14 aligned words; 32 slots cover it. Eight weights
per tap clock require 64 weight bits/clock. A shared 16-lane requantizer and
128-bit output port serialize the eight channel rows. S2b overlap also needs
independent pointwise read access and intermediate storage; the existing S1
single read port cannot supply these accesses concurrently. Rebanking and
port arbitration are required RTL work, not supplied by the estimator.

Estimated DSP demand is S1's 578 + 128 = 706 if the existing requantizers
are shared. This is arithmetic sizing, not synthesis evidence. LUT/FF/RAM
cost and clock closure remain unmeasured. The estimate pairs only adjacent
single-consumer depthwise/pointwise layers; it reports an optimistic overlap
bound without claiming that a dependency-free schedule exists. Shared
requantizer contention, stream startup and residual tensor lifetimes still
need an implemented scheduler and simulation.

## Depthwise RUN_LAYER implementation (S2b-1)

RUN_LAYER kind 1 executes groups=C, 1..87 taps, stride/dilation 1 or 2.
The command and QLR2 geometry must agree; input and output lengths are
1..800. Input masking and the signed/unsigned integer contract are unchanged.

The datapath has sixteen channel lanes and sixteen frame lanes (256 MACs).
A group preloads sixteen complete channel rows into independent local BRAM caches
(16 x 50 x 16 bytes maximum), and sixteen weights per tap into a 1,392-byte
weight cache. Both loads issue one read per cycle through the existing
four-cycle RAM port. These caches remain resident over every output frame
tile for the group. The input URAM needs no extra port or replicated tensor
bank for serialized RUN_LAYER. Cache gather supplies all 256 activations
per issue cycle. The S2b-4 implementation uses one synchronous 128-bit read
port per local cache, a rolling three-word window and a prefetched fourth word.
Five priming clocks per frame tile seed these registers. Up to fifteen serial
one-byte shifts align the first sample; frame-tile advances are multiples of
16, so the alignment cost is `(-padding) mod 16` for every tile in a layer.
Taps then shift the byte window by one or two bytes per clock. Each aligned-word
crossing inserts the prefetched word at byte 32 (byte 31 when dilation 2 crosses
an odd address). This also avoids a variable byte barrel shifter during tap
issue. The low 32 bytes feed fixed stride-1/stride-2 lane selections. Input
position masking is applied after this registered gather. The original three
full-row asynchronous read muxes per channel are removed.

Gather, signed 9x8 multiply, and 33-bit accumulate are registered stages.
The MAC uses the pointwise array's first-term bias feedback pattern rather
than a separate accumulator initialization write. Scoped `use_dsp` attributes
request one DSP48E2 per MAC without moving controller arithmetic into DSPs.
Vivado synthesis confirms 256 depthwise DSPs and 839 DSPs for the engine.
The 33-bit product register feeds the accumulating P datapath; Vivado may
retime the input/product registers within each DSP. No operands are packed.
One tap issues per clock. Bias initializes feedback, and every prefix is
checked for INT32 overflow, even if later terms cancel. Sixteen channel rows
then drain to the existing sixteen-lane exact Q31 requantizer. The shared
row stage computes explicit depthwise addresses because all frame tiles of
an sixteen-channel group finish before advancing to the next group. Pointwise
retains its existing address tracking and controller; add is unchanged.

`DW_FRAME_LANES` now defaults to and requires 16. No operand packing is used.
The local activation caches use 64 physical words (50 allocated words maximum)
with synchronous reads so Vivado can map them into block RAM. The three-word
window retains only 48 bytes per channel. Timing and area evidence is recorded
in [results/engine.md](results/engine.md).

`engine_model.depthwise_cycles` models preload, tap pipeline and row drain.
`check_depthwise_cycles.py` checks all 77 T150 layers within 2% and enforces
a 3,000,000-cycle aggregate budget. Manifest oracles, signed/unsigned edges,
masking, 87-tap dilation-2 geometry and malformed/recovery remain in `check`.

RUN_NETWORK above provides whole-program execution and on-chip tensor
liveness. Remaining S2b work includes independent activation-bank access and
intermediate storage for depthwise/pointwise overlap, plus measured
DMA-inclusive board performance.
The local caches support serialized execution; they do not establish overlap.

## Residual RUN_LAYER implementation (S2a-2)

`stt_add_ctrl` executes kind 2. The parser starts the existing unpacker once
per branch and waits for each pass to finish. Add retains all allocated
input samples, carrying valid length unchanged. Branch 0 occupies the input
bank and branch 1 the output bank; no additional full tensor bank is needed.
Each aligned word is read from both banks before its branch-1 location is
overwritten by the result. Output packing retains the normal channel-major
wire layout for partial frame tiles.

Four per-channel INT32 parameter arrays supply two independent Q31
conversions. A single signed wide multiplier is time-multiplexed over both
branches and sixteen lanes. Each product receives one half-away rounding,
with n>=64 producing zero; each branch is clamped to INT16, then summed in
INT32 and clamped once to UINT8. There is no branch ReLU. The controller
serializes conversion and issues no convolution MAC terms.

All 45 add manifest vectors and nine synthetic edges plus malformed/recovery
pass full-top simulation with stream gaps and backpressure. The 15 T150
layers take 115,482 cycles (C=256) or 230,938 (C=512), exactly matching
`engine_model.add_t150_cycles`: 450*C + 4*ceil(C/4) + 26. Response DMA is
excluded. C4 shared-scale signed logits and signed first-layer inputs use
the existing pointwise/depthwise paths. `make check` includes the add pack
and timing comparison alongside the existing suites.

RUN_NETWORK uses this serialized add controller with on-chip tensor liveness.
Independent activation-bank access and depthwise/pointwise overlap remain
future work. The
100/150 MHz OOC results belong to the complete updated top and are recorded
in results/engine.md when available.

Measured S2b-4 T150 depthwise total is 2,516,992 cycles over 77 layers,
versus 2,295,512 before the BRAM window change. Priming and serial alignment
add 221,480 cycles across 19,400 frame tiles and retain the <=3M budget.
With pointwise 5,838,248 and pipelined add 2,771,334, per-layer mode totals
11,126,574 preparation cycles.
Serialized T300 input / T150 output RUN_NETWORK takes 10,308,813 preparation
cycles, below the enforced 10.5M limit (103.08813 ms at 100 MHz, excluding
response DMA). All 558 manifest vectors and 12 network cases pass the full
regression, including 259 depthwise commands and 77 depthwise timing cases.
OOC synthesis uses 47,758 total LUTs and 839 DSPs; dw accounts for
15,363 LUTs and 256 DSPs. Routed results and the comparison with f98b5c7 are
in `results/engine.md`. The 100 MHz routed engine uses 46,527 LUTs, 42,944 FFs,
839 DSPs, 50 RAMB36 plus two RAMB18 blocks (51 BRAM36 equivalents), and 60 URAMs.
It closes with WNS +0.751 ns and WHS +0.010 ns. The 60k LUT requirement is met;
the 45k stretch target remains 1,527 LUTs away. The independent 150 MHz run
uses 46,372 LUTs, 42,280 FFs, 839 DSPs, 51 BRAM36 equivalents and 60 URAMs,
but fails setup (WNS -1.138 ns, WHS +0.011 ns). Its worst path is the parser
descriptor geometry check, from `hw_reg[11][1]` to `e_desc_reg`. This
checkpoint establishes the 100 MHz operating point; 150 MHz needs further
timing work.

|Area scope|Before f98b5c7 LUT / DSP|S2b-4 LUT / DSP|
|---|---:|---:|
|Depthwise synthesis|71,859 / 0|15,363 / 256|
|Engine routed at 100 MHz|104,894 / 583|46,527 / 839|
Per-layer values are in `../sim/engine/depthwise_t150_reference.csv`;
`results/engine.md` records test evidence and physical acceptance.
