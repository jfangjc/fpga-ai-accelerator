# QNC2 S1 wire contract, version 1

All integers are little endian. Every stream beat is 128 bits with TKEEP=0xffff,
including zero padding. TLAST marks only the final beat of a command/response.
Single clock aclk; synchronous active-low aresetn.

## Command

Four beats, sixteen u32 words:

|Word|Meaning|
|---|---|
|0|0x32434e51 (QNC2)|
|1|version 1|
|2|0 PING, 1 RUN_LAYER, 2 RUN_NETWORK; other opcodes rejected|
|3|caller tag|
|4|payload bytes, multiple of 16|
|5|input channels, 1..1024|
|6|allocated frames, 1..800|
|7|valid frames, 0..allocated frames|
|8|output channels, 1..1024|
|9|bit 0 input signed, other bits zero|
|10..15|kind and depthwise geometry; zero for pointwise (see extension below)|

PING has no payload and TLAST on header beat four. Dimensions are ignored.
RUN_LAYER payload: padded channel-major input bytes, QLR2 record header,
then bias, multiplier, shift, tiled weights. Each array is independently padded
to 16 bytes. Activations precede weights so the two-tile weight cache can drain
while later tiles arrive. Placing a full layer's weights before input would
deadlock once both cache halves fill. Positions >= valid frames are zeroed
in RTL before storage, including partial final frame tiles.

QLR2 header is sixteen u32 words:

|Word|Meaning|
|---|---|
|0|0x32524c51 (QLR2)|
|1|record bytes including this 64-byte header|
|2|output tensor id, informational|
|3|1 convolution|
|4,5|source ids, informational|
|6|ReLU flag 0/1; output signed if zero, unsigned if one|
|7,8|output/input channels, must match command|
|9..13|K=1, stride=1, dilation=1, padding=0, groups=1|
|14,15|zero|

Bias, multiplier and shift are signed INT32[Co]; multipliers must be positive,
shifts >= -30. Weights are INT8 in QNW1 tile order
[ceil(Co/32)][ceil(Ci/16)][16][32], with zero padding.
`model.qnc2_export.run_layer` encodes this command.

## Response

Four header beats followed by padded channel-major [Co,T] output on success.
Errors and PING have only the four header beats. TLAST marks the final beat.

|Word|Meaning|
|---|---|
|0,1|0x32534e51 (QNS2), version 1|
|2,3|status, echoed tag (zero if the four-beat header was incomplete)|
|4,5|64-bit clocks from first accepted command beat through response preparation|
|6|input starvation clocks (receiver ready, sender not valid)|
|7|issued reduction terms|
|8|unpadded output bytes, or zero|
|9..12|Co, allocated frames, valid frames, signed-output flag; zero on errors/PING|
|13|opcode|
|14|accepted beat count when first parser error latched|
|15|engine version 0x00010002|

Status: 0 OK, 1 magic/version, 2 opcode, 3 length/TKEEP/TLAST,
4 descriptor/limits/parameters, 5 accumulator overflow, 6 idle timeout,
7 internal invariant violation. Malformed commands drain through TLAST before
response; magic in payload never resynchronizes the parser. Early TLAST ends
the drain immediately. A stream with no TLAST requires the idle watchdog or
soft reset. Watchdog advances only while receiver ready and sender idle.

## AXI-Lite registers

32-bit aligned accesses; address/data writes accepted independently. WSTRB
applies to writable byte lanes. Unmapped reads return zero; writes ignored.
All AXI responses are OKAY.

|Address|Register|
|---|---|
|0x00|CONTROL bit 0 soft reset pulse|
|0x04|STATUS bit 0 busy, bit 1 responding, bit 2 IRQ level|
|0x08|VERSION 0x00010002|
|0x0c|IRQ_ENABLE bits 0 completion, 1 error|
|0x10|IRQ_STATUS bits 0 completion, 1 error, W1C|
|0x14|last tag|
|0x18|last status|
|0x1c|idle watchdog clocks; default 2^24, zero disables|
|0x20,0x24|last completion cycle count low/high (includes response transmission)|
|0x28,0x2c|response/error counts|
|0x30,0x34|last issued terms/input starvation clocks|
|0x38|scratch, byte-writable|

IRQ is level, latched at response completion, and remains until W1C or soft
reset. Completion wins over simultaneous acknowledgement. Soft reset discards
partial commands/responses and pending IRQ; register settings remain. Software
must abort/reset both DMA channels along with engine soft reset.
Arm S2MM before MM2S. Flush command data before DMA and invalidate response
buffers before/after DMA; the HP ports are not cache coherent.

## S2 program format (export only)

QNP2 programs from `qnc2_export.export` begin with a 16-byte header: magic
QNP2, version 1, record count, total program bytes. QLR2 records follow, with
parameters then tiled weights. S1 RUN_LAYER omits the QNP2 program header.
RUN_NETWORK executes this program format. Individual depthwise and add records execute through RUN_LAYER.

### RUN_NETWORK envelope

Engine version 0x00010002 enables opcode 2. Earlier versions reject it.
`model.qnc2_export.run_network` implements the following envelope without
changing any QNP2 record bytes or ordering:

|Command word|Value|
|---|---|
|0..4|QNC2 magic, version 1, opcode 2, caller tag, padded payload bytes|
|5..9|64, allocated input frames, valid input frames, 29, 1 (signed input)|
|10,11|186 records, QNP2 program bytes including its 16-byte header|
|12..15|zero|

Payload is INT8 channel-major `[64,T_in]` features padded to 16 bytes,
then the complete unmodified QNP2 program. Allocation limits are
1..640 allocated input frames, valid 0..T_in, and at most 320 frames after
C1. Exactly 186 records must produce signed 29-channel logits. RTL accepts the exact fixed exported descriptor sequence; modified topology
is rejected even if it is otherwise a valid QNP2 graph. Each source
must precede its consumer, convolution channels must agree, and add inputs
must have identical shapes, valid lengths and signed representation.
QLR2 sizes must match their geometry/arrays, not merely remain in bounds.
The exporter checks these constraints and rejects trailing/truncated data.

The response uses the existing QNS2 header: words 4/5 total preparation
clocks, word 6 input-starvation clocks, word 7 aggregate issued vector terms,
words 8..12 output bytes/29/T_out/valid_out/1, and word 13 opcode 2.
There is one padded INT8 `[29,T_out]` payload; only its final beat has TLAST.
Error responses have no logits. Detailed compute, weight-wait and transition counters are simulation-visible
RTL signals, not additional response words. Transition accounting includes
feature/program/parameter reception and layer startup; compute excludes
controller waits for unavailable weights. Existing error drain/watchdog/reset rules apply to the whole
packet; no intermediate record has TLAST.

Driver contract for the command: allocate a 64-byte-aligned TX buffer
of `64 + align16(64*T_in) + program_bytes`, and RX capacity of
`64 + align16(29*ceil(T_in/2))`. A single simple-mode MM2S transfer sends TX.
Alternatively an SG chain has two segments: header+features, then the immutable
QNP2 program; only the last descriptor terminates the AXIS packet. Arm S2MM
first. Clean all written TX ranges and SG descriptors before ownership passes
to DMA, prepare/invalidate RX before DMA, then invalidate the completed RX
range before CPU reads. An immutable program can retain its DMA mapping, but
must be cleaned after any modification. CPU and DMA must not concurrently
write these buffers. Validate status, tag, lengths and response TLAST before
decoding only valid logits. Oversize phrases use per-layer mode.

The current driver's documented 8 MiB TX allocation is insufficient for this
20,037,392-byte program. Network mode requires a larger contiguous allocation
or SG-mapped program storage. This checkpoint does not change the driver.

## S2a RUN_LAYER extension

`run_layer` can encode all 558 manifest vectors. The RTL executes pointwise
depthwise, and residual add records.
Existing pointwise commands are byte-for-byte unchanged.

Command word 10 selects 0 pointwise, 1 depthwise, or 2 residual add. For
depthwise, words 11..14 carry taps, stride, dilation, padding; word 15 stays
zero. For add, words 11..15 stay zero. Command input channels are the full
channel count (not channels per group). For depthwise the QLR2 input channel
field remains 1, groups equals output channels, and taps/stride/dilation/padding
must agree with the command. Limits: taps 1..87, stride/dilation 1 or 2,
padding 0..86, input/output allocated lengths 1..800. Output allocated and
valid lengths follow the convolution formula in quant_spec.md.

Depthwise payload is input, QLR2, bias, multiplier, shift, tiled weights.
Add payload is branch 0, branch 1, QLR2, multiplier 0, shift 0, multiplier 1,
shift 1. Every segment is independently padded to 16 bytes. Add branches are
signed INT8 with equal [C,T] shape and shared valid length; QLR2 op is 2 and
ReLU is 1. Add does not mask its inputs; it preserves the supplied valid
length. Each branch is requantized separately and clamped to INT16 before
the sum is clamped to UINT8. QNP2 records already represent both operations
in a linear stream and need no format change.

## Implemented depthwise extension

RUN_LAYER kind 1 is now accepted by the RTL, using the depthwise record and
limits above. Kind 2 (add) is also accepted. The response words 8..11 use
output allocated/valid lengths for depthwise; input word 6 remains the input
allocated length. A computed nonpositive valid length is returned as zero.
The implementation preserves signed output for the exported depthwise
records (QLR2 ReLU=0). Existing pointwise commands and responses are unchanged.


## Implemented residual extension

Kind 2 requires signed inputs and identical command input/output channels.
The QLR2 geometry words 8..15 must all be zero, op=2, ReLU=1, and channels
must match the command. Both multiplier arrays must contain positive signed
INT32 values and both shifts must be >=-30. Malformed records drain through
TLAST using the existing error/recovery path. The final shift-array beat
carries command TLAST; no bias or weight payload follows.

Both allocated and valid lengths are preserved. Padded positions inside T
are converted as supplied (no convolution-style valid mask on add). The
response is UINT8, including those positions; the following convolution
must mask at its own input boundary. Issued MAC terms is zero for add.

### S2b depthwise issue counter

For the parallel depthwise implementation, response word 7 counts vector tap
issues: one issue updates sixteen channels by sixteen frames (256 MACs,
including padded channel/frame lanes). It excludes cache fills and row drain.
Response preparation cycles remain word 4/5; they are the performance metric.
No payload, geometry, or arithmetic fields change.
