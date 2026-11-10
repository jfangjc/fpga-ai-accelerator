# CPU runtime

Build on KV260: `make -C sw/qnrt all qnrt_transcribe CFLAGS="-O3 -mcpu=cortex-a53 -std=c11 -Wall -Wextra"`.
No third-party runtime dependencies: libc, libm, pthread. FLAC is converted losslessly
to mono 16 kHz PCM16 WAV by `tests/cpu/prepare.py` on the host.

`qnrt_load(&context, path, max_frames, threads)` validates and loads QNW1. Threads
must be 1 through 4. `qnrt_input_scale` returns the header float32 scale.
`qnrt_run(context, features, allocated_T, valid_T, &logits, &allocated_out_T,
&valid_out_T)` returns context-owned signed byte logits in channel-major `[29,T]`.
Read only valid frames for CTC. `qnrt_layer` executes a zero-based descriptor into
caller-provided storage; the caller supplies graph-correct shapes and signedness.
All calls return zero on success, negative on failure. `qnrt_free` joins workers.
Use independent contexts for simultaneous inference; one context is not reentrant.
Layer API output must not alias inputs. Its output capacity must cover descriptor
channels times the convolution output length.

Descriptors retain signedness inferred from producers. Internally activations are
frame-major, with each frame's channel stride rounded to 64 bytes. Pointwise
weights are repacked at load time as `[ceil(O/4)][ceil(K/16)][4][16]`, with
64 spare bytes for the pipelined final loads. A register-blocked AArch64 kernel
computes four outputs at four frames per call. SMULL and SMLAL2 combine two
signed byte products in each int16 lane; SADALP accumulates pairs into int32.
Unsigned activations are mapped to signed bytes with XOR 0x80 and corrected by
adding `128*sum(weights)` to the bias. Each int16 lane is bounded by
`2*127*128 = 32512 < 32768`. Applying the same pairing directly to UINT8 would
allow `2*127*255 = 64770` and is unsafe. Int32 partial sums and corrected bias
use NEON modular arithmetic; the loader proves the final mathematical result
fits INT32, so its final bit pattern is exact even if an intermediate wraps.
The original scalar arithmetic uses int64 and checks the final result.

Pointwise work units cover 64 output channels and 16 frames. This aligns
channel partitions to cache lines; work is partitioned over outputs and time.
Depthwise weights use `[ceil(C/64)][tap][64]` int16 layout; each thread stages
64 channels over a 64-frame output tile and its dilation halo. Depthwise SIMD
runs along channels, with four frames sharing weight loads. These staging
buffers can exceed 32 KB for large kernels; this is a candidate for tuning
against actual board counters, not a proven L1-resident kernel. No dot-product
ISA is required. Requantisation uses exact int64 products and SRSHL with a
negative-product adjustment to preserve one half-away rounding, including n=63;
channels with n>=64 have a derived zero multiplier.

The loader checks all header fields, reserved bytes/words, finite positive input
scale, graph references/channels/signedness, supported groups, dimensions, aligned
payload ranges and exact sizes, weight padding, forbidden -128 weights, positive
multipliers and legal shifts. It conservatively rejects any convolution whose
conservative signed accumulator interval exceeds INT32 bounds.
This bounds the mathematical accumulator, but can reject a blob whose particular
inputs would be safe. The offset pointwise implementation may wrap intermediate
int32 lanes as described above. Payload aliasing is allowed by QNW1.
Every input load respects valid length without modifying caller tensors.
Residual branches round separately, clamp to int16, add then clamp to uint8.

Workers persist for context lifetime, spin briefly between layers, then sleep on
condition variables. Allocation occurs at load time. A liveness pass reuses graph
activation buffers after their last consumer; outputs never alias their inputs.
One context is not reentrant. Weight loading briefly retains the original blob.

`qnrt_run_frames` accepts already-quantised frame-major `[T][64]` input;
`qnrt_run` accepts channel-major `[64][T]`. Both return channel-major logits.
`qnrt_profile(ctx, seconds)` enables caller-owned per-descriptor wall times;
NULL disables timing. `qnrt_layers`, `qnrt_layer_kind`, `qnrt_macs`, and
`qnrt_output_frames` expose metadata for the A/B harness. MAC counts include
nominal convolution padding; pass valid frames to count the valid-length graph.

`qnf_features` implements the centered float frontend, including reflection only
at utterance boundaries, preemphasis, non-periodic Hann, radix-2 FFT, Slaney mel,
log guard and sample-std normalization. Output is padded to a multiple of 16.
It allocates frontend scratch per utterance. `qnf_state` persists double-precision
Welford statistics for running mode; `qnf_prior` reads the exported prior JSON.
`qnf_stream_new(prior)` creates a separate running-normalisation stream (NULL
prior starts at count zero). `qnf_stream_push` accepts float PCM chunks at 16 kHz
and emits frame-major `[frames][64]` features, up to the caller's frame capacity.
Frame j is ready after `160*j+256` samples. Preemphasis and reflection history
persist across chunks. Push with zero samples to drain a backlog. `flush` marks
the actual utterance end and emits reflected tail frames; repeat until zero.
Total input must exceed 256 samples for flush. `reset` starts a new utterance,
`state` exposes current Welford statistics, and `free` releases storage. Invalid
nonfinite chunks are rejected before state changes. Storage grows with an
undrained backlog; callers should drain regularly. Quantize emitted floats with
`qnf_quantize` before calling `qnrt_run_frames`. Final phrase utterance
normalisation still uses `qnf_features` on the retained phrase waveform.
`qnf_quantize` performs float32 division, half-away rounding and int8 saturation.

CLI: `qnrt_transcribe quartznet_int8.bin clip.wav --mode utterance --threads 4`.
Use `--mode running --prior running_prior.json` for causal normalization.
`--bench` performs one warmup plus five measured frontend and network runs.
`--runs N` selects one warmup plus N measured samples (1 through 10000).
`--features file` exports float and quantized frontend diagnostics.

Reproduce vectors: generate `vectors.tsv` from manifest using `tools/manifest.py`,
then run `qnrt_test quartznet_int8.bin vectors.tsv 4`. Test data must be available
under `vectors/`; independent network fixtures under `clips/` are also required
for full validation. `tools/prepare.py` creates these from trusted Python weights
and the saved test subset. Never commit weights, audio, generated features or binaries.



A buffer's returned time stride is `allocated_out_T`, not `valid_out_T`:
`logits[class_id * allocated_out_T + time]`. For greedy decoding compare signed
requantized logits, choose the lowest ID on ties, collapse consecutive IDs,
then remove blank 28. The CLI applies these rules. The network and per-layer
functions leave caller input buffers unchanged, including padded positions.

`make qnrt_test_basic qnrt_test_scalar` builds regression/performance alternatives.
`make peak` builds the AArch64 widening-MAC ceiling microbenchmark. Run it only
on an otherwise idle board. `tools/report.py` creates measured result tables
from copied board logs; it refuses any retained IRQ-contaminated clip.

Host scalar and QEMU Cortex-A53 NEON validation is recorded in
`docs/results/cpu.md`. Native idle-board windows now measure about 13.4 GMAC/s at four threads; see
`docs/results/cpu.md` for the accepted samples and environment.
`qnrt_test_basic` currently uses the same optimized NEON
path as the default build; it is not an independent performance alternative.

For the overflow proof, one direct product is at most `127*255=32385` in
magnitude and fits int16; two UINT8 products must never share that lane.
Depthwise widens inputs and accumulates each product into int32. The loader
sums per-weight minima/maxima plus bias in int64 and rejects bounds outside
INT32. Each contribution interval contains zero, so direct partial sums also
lie within those bounds. Pointwise uses signed offset inputs and the 32512
two-product bound above. Modular NEON reductions and bias correction produce
the loader-bounded original convolution's exact final signed result. Scalar C
uses int64 and never relies on signed overflow.

`make -C sw/qnrt qnrt_bench` builds the window harness: `qnrt_bench blob input.i8
frames valid threads runs`. Input is channel-major INT8; run 0 is warmup.
A separate pass prints layer wall times outside network measurements. Keep the
profiler array alive until disabled; each inference overwrites it. Times include
worker dispatch. Window inference retains no convolution state between calls.

On the idle board, `python3 qnrt/tools/final_eval.py --runs 20 --phrase phrase.wav`
collects correctness, scaling, peak and 40-clip results into `cpu_final.json`.
The phrase must be approximately 16 seconds (15.5 through 16.5 seconds); retain
the natural utterance length. Add `--phrase-reference clips/phrase_reference.json`
with the manifest row to score its transcript. Phrase timing uses 20 measured runs. Render with
`python3 qnrt/tools/report.py cpu_final.json > cpu_final.md` for review.
The report uses matching thread counts of peak's `int8_sched` variant, whose
output already contains five-sample medians. Test log parsing with
`python3 tests/cpu/peak_log.py` on the host.

Final native validation and measurements are complete (2026-11-02). Four-thread
2/3/4 s windows take 145.241/217.906/285.558 ms median, about 13.1-13.5 GMAC/s.
The 40-clip front-end + network RTF is 0.079501 utterance / 0.080807 running;
WER is 4.3632% / 4.8349%, peak RSS 43 MiB. The natural 16.02 s utterance
7176-92135-0014 takes 1.277526 s median / 1.290181 s p90 over 20 samples,
with 3/46 word errors. Full tables, profiling, timing boundaries and limitations
are in docs/results/cpu.md. The collector holds the shared board flock throughout
all commands, records top snapshots, and rejects batches failing idle checks.
