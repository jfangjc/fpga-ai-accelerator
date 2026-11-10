# QuartzNet integer contract (QNW1, version 1)

The executable definition is `model/golden_int8.py`. All tensors are channel-major
`[C,T]`, time contiguous. All convolution padding is literal integer zero.
Weights are symmetric INT8 [-127,127], per output channel, zero point zero.
Input log-mel features and depthwise/linear outputs are INT8 [-128,127]. ReLU
outputs are UINT8 [0,255]. Every zero point is zero. Descriptors identify ReLU
and therefore output signedness; input signedness follows its producer.
The current blob's fixed input scale is FLOAT32 `0.03692806139588356`, read
from header byte 32. Quantize normalized input with float32 division followed
by half-away rounding and saturation to [-128,127]; network execution thereafter
uses only integer descriptor parameters.

Float calibration is separate from integer inference. Weight scale is
`max(abs(weight[channel]))/127`, lower bounded by 1e-12 and
`abs(folded_bias[channel])/(input_scale*2^30)` to represent constant BN channels
without overflowing the integer bias. Activation scales are
positive per tensor; input uses /127, ReLU /255, other tensors /127. Scale
estimation uses maximum over per-utterance absolute percentiles on dev-clean.
No test-clean utterance contributes calibration statistics. These choices are
validated on the recorded 40-clip subset: 4.5991% INT8 versus 4.3632% float
with utterance normalization (see `results.md`).

## Arithmetic

Cross-correlation uses weights `[O,I/groups,K]`; tap j reads
`input[i,t*stride+j*dilation-padding]`. Missing samples are zero. No kernel
reversal. Depthwise uses groups=C, I/groups=1. Pointwise uses groups=1, K=1.
The output time count is `floor((T+2P-D*(K-1)-1)/S)+1`.

For output channel c, input scale sx, weight scale sw[c], output scale sy:

```
bias[c] = round_half_away(float_bias[c] / (sx*sw[c]))
acc[c,t] = bias[c] + sum_i,j(input[i,...] * weight[c,i,j])
r[c] = sx*sw[c]/sy
# frexp(r) = mantissa * 2**exponent; 0.5 <= mantissa < 1
mult[c] = round_half_away(mantissa * 2**31)
if mult[c] == 2**31: mult[c] /= 2; exponent += 1
shift[c] = -exponent
p = int64(acc[c,t]) * int64(mult[c])
n = 31 + shift[c]                     # require n >= 1
if n >= 64: q = 0                     # tiny-scale channels
else:
    q = sign(p) * ((abs(p) + (1 << (n-1))) >> n)
out[c,t] = clamp(q, 0,255) if relu else clamp(q,-128,127)
```

There is ONE rounding after the wide multiplication. This is not the NEON
`SQRDMULH` followed by a separately rounded shift. Implement with equivalent
64-bit arithmetic to avoid double rounding. Arithmetic right shift on negative
values is not used in the pseudocode. `sign(0)=0`. Bias and every accumulated
result must fit signed INT32; export/runtime must reject overflow, never wrap.
Products fit INT64. Weight/input quantization uses the same half-away rule.
For n>=64 every legal INT32*positive-Q31 product rounds to zero; detect this
case before shifting. Shift n=63 is valid and its rounding addition fits INT64.

BN uses epsilon 0.001: `a=gamma/sqrt(var+epsilon)`, `W'=a*W`,
`b'=beta+a*(b-mean)`. Fold only into the pointwise or ordinary convolution;
there is no ReLU between depthwise and pointwise. In a residual block the last
pointwise and residual projection both have signed output without ReLU.

## Residual addition

This version uses explicit scaled addition, not K concatenation. Each branch
has its own quantized signed output. Convert both to the add output scale with
the Q31 procedure above, clamping each converted branch to [-32768,32767]
and storing temporarily in INT32. Sum the two converted branches in INT32,
then clamp once to [0,255]. The branch clamps and two separate rounding steps
are part of the contract. The add descriptor carries both multiplier/shift
arrays. Do not insert a ReLU on either branch before addition.

## Lengths and logits

Frontend output includes normalized zero padding to a multiple of 16; carry
valid length `floor(samples/160)+1` separately. Before EACH convolution, clear
all input positions beyond valid length. Propagate valid length by the formula
above (first convolution halves with ceiling). This emulates NeMo conv_mask.
Do not discard padded tensor positions early, or count them in CTC decoding.

The 29 final logits use ONE signed INT8 output scale shared across classes.
Per-channel accumulator integers cannot be compared directly because their
scales differ. Argmax is over requantized logits; ties choose lowest class id.
This may differ from float argmax and must be assessed through WER. Vocabulary
is space, a-z, apostrophe; blank index 28. Collapse consecutive identical ids
BEFORE removing blanks.

## Frontend and streaming

NeMo v1.0.0 reference source:
https://github.com/NVIDIA/NeMo/blob/v1.0.0/nemo/collections/asr/parts/features.py

Use preemphasis before reflect padding, non-periodic Hann, Slaney mel area
normalization, log(mel + 2^-24), sample standard deviation plus 1e-5. Dither is
disabled in eval. A centered 512-point frame needs 256 future samples (16 ms).
Only reflect at actual utterance boundaries; streaming chunk boundaries must
retain waveform/preemphasis history and defer incomplete frames. Calling
`mel_power` on independent chunks does not reproduce whole-utterance features.

Running normalization uses causal Welford sample statistics including
the current frame. Calibration creates a dev-only prior with count=100,
global log-mel mean and m2=99*global population variance (per band).
INT8 running WER evaluation uses that prior; export writes `running_prior.json`
for PS initialization. The standalone API without a prior starts with count=0:
the first frame outputs zero. State is returned by `log_normalize`;
callers must retain it. `features` is the utterance convenience wrapper.
Running normalization measures 4.8349% full-clip WER on the recorded subset.
It is suitable for preview experimentation; final phrases should use utterance
normalization (4.5991%). It does not restore missing convolution context.
