# Application board results, 2026-11-03

## Static front-end scratch, 2026-11-05

Whole-phrase finals now use `qnf_features_ws` with a static pre-faulted
1.5 MiB scratch and a static packed-normalisation copy, so no final allocates
on the heap. The output is bit-identical to `qnf_features`. In a 30-minute
soak this removes the 308 KiB heap step: RSS after 150 s rises 56 KiB in six
small steps and then stays flat for the last 615 s. Preview p95 is 209.0 ms,
there are 0 merges and 0 lost samples, and all 196 finals are identical. T1
WER is 39/848 and T1 preview p95 is 204.9 ms. See
[T2: Static front-end scratch](t2.md#static-front-end-scratch-final-configuration-2026-11-05).

## Phrase-indexed emission, 2026-11-05

The new full 30-minute soak restores the preview stretch target with
`--final-backend auto --final-threads 3`, which remains the default.
Preview p50/p95/max is **207.904/208.768/224.181 ms**, with **zero merges and
zero sample loss**, versus ordered auto's **207.906/658.309/2772.252 ms**.
It captures **180,079/180,079 frames**, serves **14,673 previews / 196 finals**,
and verifies **15,262/15,262 JSONL/TCP events and stage rows**. All 196 final
text/word-end/boundary/feature-length records are byte-identical after canonical
serialization to the original final records. Routing labels and accumulated
captions intentionally differ. This run omits synchronous final dumps; it does
not repeat the earlier 196-final raw-logit byte comparison.

A pending phrase retains its last stable preview in amber as tentative-committed,
while the following phrase remains live. A final replaces only its own phrase.
Each outstanding phrase keeps separate stitch state; finals never supply tokens
to another phrase's preview CTC. Every event and late-join snapshot includes the
committed prefix and phrase-indexed segments/states. The independent caption
reducer verifies all events, including **1,389 previews displayed before an
earlier final**. Ready-to-emission preview wait p95/max is **0.203/3.382 ms**.
The framebuffer, PPM, TCP client and SD log share this model; latency still
ends at software framebuffer completion.

| Configuration | Preview p50 / p95 / max, ms | Final latency p50 / p95 / max, ms | Merges | strict_t2_pass |
|---|---:|---:|---:|---|
| PL serial finals | 207.975 / 209.396 / 1037.159 | 1254.101 / 1345.728 / 1425.012 | 506 | false |
| Ordered auto / 3 CPU threads | 207.906 / 658.309 / 2772.252 | 1651.916 / 2238.931 / 3546.567 | 0 | true |
| Phrase-indexed auto / 3 CPU threads | 207.904 / 208.768 / 224.181 | 1657.830 / 2233.435 / 3089.455 | 0 | false |

Long finals still cost more on CPU. They are **173/196** of the soak phrases;
median compute in the retained PL/ordered-auto logs is **464.632/857.660 ms**,
with sub-millisecond median submit queues. Short PL final compute is unchanged,
but their earlier ordered-emission wait is removed. One/two CPU threads make
finals slower in controlled 24 s smokes, so three nice-5 threads on cores 1-3
remain selected. New speech-end-to-final-display p50/p95/max is
**1657.830/2233.435/3089.455 ms**. This preserves the original final-latency
definition, including the 51 forced phrase caps; it is not a median final win.

**`strict_t2_pass=false`**, solely because post-150 s RSS rises **308 KiB**
(**190,296 -> 190,604 KiB**) before plateauing. The endpoint predicate is
unchanged. The preceding strict pass sampled teardown; this run samples live
memory at its endpoint. Neither result establishes perfectly flat steady-state
RSS. New power is **4.091 W mean / 5.110 W sampled max**; PS process CPU is
**46.074% mean of one A53**. A contaminated 185 s pilot during unattended OS
upgrades is retained separately and excluded from these performance figures.

Strict native and host checks pass, including the actual emitter's delayed-final
test, caption replacement/snapshot tests and 5,800 stitch comparisons. The
completed 24 s native smoke independently checks captions and a late snapshot.
Per-five-minute measurements, final scheduling diagnostics, reproduction and
remaining RSS/final-latency limitations are in
[T2: Out-of-order caption emission](t2.md#out-of-order-caption-emission).

## Independent final routing, 2026-11-05

The matched **30-minute no-dump run passes the unchanged strict T2 harness**:
**180,079/180,079 frames**, **zero merges/loss/errors**, **14,673 previews /
196 unchanged finals** and **15,262/15,262 TCP events and stage rows**.
Preview p50/p95/max is **207.906/658.309/2772.252 ms**. The p95 core limit
passes; the 250 ms stretch target regresses because ordered display still
waits for earlier CPU finals. Zero merges is not a display-latency improvement.

Active-capture RSS varies only **308 KiB**, from 189,516 to 189,824 KiB, then
has an approximately 18-minute flat plateau. Memory maps attribute the entire
step to a retained heap extension. The strict RSS endpoint passes because the
last sample catches cleanup and drops to 57,556 KiB; it must not be described
as perfectly flat live RSS. The primary predicate remains unchanged, with its
sampling/teardown limitation documented in T2. Power is **4.095 W mean /
5.070 W sampled max**, and PS process CPU is **46.070% mean of one core**.

All 196 original final fields/captions/stitch indices agree in the matched
run. The separate instrumented run compares **2,613,944 final logit bytes**
with PL without a difference. It also exposes synchronous `--dump` I/O stalls,
so performance acceptance uses the no-dump repeat with the same executable.
All-phrase last-voiced-block-to-final-display p50/p95/max is
**1651.916/2238.931/3546.567 ms**; for 145 non-cap speech boundaries it is
**1627.482/1801.105/2748.730 ms**. Forced 15 s caps are identified separately.

Long PL finals now use an independent CPU INT8 backend, with pooled inputs,
preallocated CPU activation tensors and an ordered copied-logit completion
ring. The measured default is `--final-backend auto --final-threads 3`:
short finals stay on PL RUN_NETWORK; finals above 640 padded frames use nice-5
CPU threads on cores 1-3. The final native smoke has zero loss and zero merges
across 2,479 frames, with all four final inputs/logits byte-identical to PL
(35,496 compared logit bytes). Strict native and host CTC/stitch checks pass.

Independent inference removes final-induced inference blocking, but preserving
event order still delays later-phrase display behind an earlier final. The
24-second full-output smoke's preview p50/p95/max is
204.664/311.792/769.169 ms; this exceeds the preview stretch target at p95.
The output-ordering delay is exported explicitly, so zero merges cannot hide
it. Detailed routing comparisons and final evidence are in
[T2: Final routing](t2.md#final-routing).

The new 40-clip T1 batch preserves **39/848 final errors**, all **42 finals**
(including word/stitch indices), **2,539 previews**, zero merges and valid
latency samples. Raw preview p50/p95/max is **204.307/205.491/208.084 ms**;
stable-preview L_word is **604.313/664.452/994.836 ms** (789 observed,
59 censored words). The 66-event host replay also preserves every compared
caption/state/stitch field and final input/logit byte.

## Soak follow-up, 2026-11-05

The current app uses a bounded 120-second hardware-counter/CLOCK_MONOTONIC fit
after the startup FIFO probe. An independent 660-second paced replay measured
**15,999.747 samples/s (-15.802 ppm)** with zero capture loss; the original T2
trace fits -20.935 ppm. This ratio explains drift introduced by extrapolating
the initial anchor forever at a nominal 16 kHz. NTP discipline contributes:
CLOCK_MONOTONIC measured +6.336 ppm relative to CLOCK_MONOTONIC_RAW.

Stages now stream through the low-priority writer, with a fixed log-buffer
pool. The original 72-byte/event stage array's page residency predicts
34.6 KiB/min, close to the observed 33.0 KiB/min RSS slope; it was bounded,
not evidence of an unbounded leak. Every new merge and update over 300 ms has
an auditable cause record. Long PL finals remain non-preemptible per-layer
requests; changing their execution safely needs driver support. Final
arithmetic and scheduling order are unchanged.

Full follow-up methods, evidence and acceptance limits are in
[T2: Soak follow-up](t2.md#soak-follow-up). The results below are historical
measurements and retain their original bitstream/metric qualifications.

The corrected 30-minute soak captures **180,079/180,079 frames with zero
drops/errors**. Five-minute preview p95 stays **209.064-209.534 ms**; overall
p50/p95/max is **207.975/209.396/1037.159 ms**. All 506 merges are attributable:
501 during finals and five during immediate post-final recovery. All 157
previews above 300 ms are final-related. All 196 final events match the
original run. Sampled power is **4.068 W mean / 5.010 W max**.

Recording is bounded, but RSS is not perfectly flat: bucket medians increase
**180 KiB** over the run, and the fitted all-sample slope including temporary
final tensors is **+23.103 KiB/min**, with **215580 KiB** peak. Fixed static-page
and allocator residency remains; this is not an indefinite-session or general
leak-audit pass. The strict zero-merge/non-growing-RSS harness still fails.

The 40-clip PL rerun has **2,539 previews, 42 unchanged finals, zero merges**.
Using the preceding measured sample rate to calibrate short-clip startup
anchors gives **204.210/205.650/207.941 ms** p50/p95/max; the correction is
at most 0.1995 ms. Final and preview WER remain 4.599057% and 7.311321%.
The app's optional `--sample-rate` applies that startup calibration in real
time; the rolling fit supersedes it after ten bins. CPU T1 was not rerun.

## I3: engine outputs to captions (2026-11-03)

**Functional I3 with the current stt-full bitstream passes at the network
level on 6 clips / 8 phrases. 7/8 finals equal the Python full pipeline; the
eighth differs by one character for a front-end reason, not the engine.**
The bitstream is the older engine (VERSION 0x00010001, RUN_LAYER only), so
every inference is the 186-command per-layer graph (~2.24 s per 3 s
window). Previews therefore merge heavily, as expected. This is not a
latency result.

Setup: KV260, stt-full loaded, u-dma-buf 0-3 as in docs/results/i2.md, native
strict build of `4d66c4c` (+ `i3_board.sh` path fix), in `~/stt/i3/src`.
Program `~/stt/i2/program.qnp2` (SHA-256 ee07f475...f583, the
`qnc2_export.export` of `~/stt/cpu/quartznet_int8.bin`). Each clip ran paced
`--source pl-inject` twice, `--backend pl` then `--backend cpu`, 3 threads,
with a TCP client on loopback port 8765, `--dump`, PPM and a `/dev/fb0`
readback, all under one `flock ~/stt/board.lock` batch:

```sh
flock /home/ubuntu/stt/board.lock sh src/tests/app/i3_board.sh \
  /home/ubuntu/stt/i3/src/build/sw/app/sttcap /home/ubuntu/stt/i2/program.qnp2 \
  /home/ubuntu/stt/i3/run1 7021-79740-0013 1089-134691-0023 1580-141084-0049 \
  6829-68771-0025 2094-142345-0034 61-70968-0054
# host, after fetching run1/:
.venv/bin/python tests/app/i3_check.py build/sw/app/measurements/i3/run1 --json ...
.venv/bin/python tests/app/fb_check.py run1/<clip>.<backend> ...
```

Clips were chosen to cover two-phrase clips and finals longer than the
800-frame RUN_LAYER limit (tiled C1). Checker output:
[`results/app/i3_results.json`](../../results/app/i3_results.json).

|Clip|Phrases (valid frames)|PL commands|PL final compute ms|PL logits == golden_int8|PL == CPU (text, input, logits)|PL == Python pipeline|C vs Python INT8 input bytes differing|TCP finals|fb == PPM pixels|
|---|---|---|---|---|---|---|---:|---|---|
|7021-79740-0013|1 (756)|186|5808.5|1/1|1/1|1/1|4|1/1|153,472*|
|1089-134691-0023|1 (765)|186|5804.8|1/1|1/1|1/1|4|1/1|153,600|
|1580-141084-0049|2 (224, 541)|186, 186|1572.1, 3923.8|2/2|2/2|2/2|4|2/2|153,600|
|6829-68771-0025|2 (615, 196)|186, 186|4705.8, 1560.0|2/2|2/2|2/2|1|2/2|153,472*|
|2094-142345-0034|1 (868)|187 (tiled)|6960.6|1/1|1/1|1/1|5|1/1|153,472*|
|61-70968-0054|1 (846)|187 (tiled)|6667.0|1/1|1/1|**0/1**|8|1/1|153,600|

- **Engine to captions:** all 8 finals' padded logits (70,528 bytes,
  including the two tiled >800-frame finals) equal `golden_int8.network`
  on the exact INT8 features the app submitted, with matching allocated and
  valid lengths. The CPU backend received byte-identical inputs and produced
  byte-identical logits. PL and CPU final texts are equal for 8/8 phrases.
- **Segmentation:** the Python `stream_sim.phrases` on each replayed clip
  (+0.8 s tail) gives the same phrase count, end samples and valid lengths
  for all 8 phrases.
- **Python INT8 golden pipeline** (`frontend.features` -> `quantize` ->
  `golden_int8.network` -> greedy CTC): 7/8 texts equal. For 61-70968-0054
  the app says "...the care of montfichet" and Python says "montvichet".
  The engine is not the cause: the app's own INT8 input decodes to
  "montfichet" under `golden_int8`, and the C front end's quantised input
  differs from Python's in 8 of 54,272 bytes (1-5 bytes on the other clips).
  This is the known float front-end portability gap (final WER 4.60% vs
  Python 4.25%, results below).
- **Display path:** `caption.ppm` is written on every update. The
  `/dev/fb0` readback after each run equals the PPM in RGB565 on all
  153,600 caption pixels, except (*) one 8x16 cell at x 0-7, y 112-127.
  There the kernel fbcon blinking text cursor overwrote black. Example
  readback: [`results/app/i3_fb_2094-142345-0034.png`](../../results/app/i3_fb_2094-142345-0034.png)
  ("and there's linen in the house ... isn't made up"). Framebuffer
  contents are not proof that a monitor showed them; no monitor was observed.
- **TCP:** every final arrived over TCP and equals the logged final. The
  server accepts lazily at the next event, so the client missed the initial
  `Listening` event, and in two runs a second early event.
- **Previews (PL, per-layer):** 6-8 served per clip, with 54-64 merged.
  Per-preview compute rises from ~0.35-0.54 s on short windows to 2.23-2.25 s
  on full 3 s windows; renderer L_update reaches ~4.6 s (up to 9.2 s at a
  phrase boundary). CPU-backend previews on the same clips: 32-38 served,
  compute <= 0.32 s.

Driver per-layer overhead on the same bitstream is in docs/results/engdrv.md.
The RUN_NETWORK path (one command, ~100.7 ms of engine time per 3 s window
in cosim) cannot run until the timing-closed 0x00010002 stt-full and a
>= 24 MiB u-dma-buf 2 exist. The app needs no change for it.

### T1 paired A/B harness: dry run

`tests/t1/bench_app.py --ab --power --clips 7021-79740-0013 1580-141084-0049
2094-142345-0034 --board-dir /home/ubuntu/stt/i3/bench --sttcap
/home/ubuntu/stt/i3/src/build/sw/app/sttcap --output build/sw/app/measurements/ab-dry`
(Windows PC). Each clip ran on both backends back to back, CPU first on
even-indexed clips. Every run used the board mutex, and power was sampled in
parallel. Summary: [`results/app/ab_dryrun_results.json`](../../results/app/ab_dryrun_results.json);
a 2-clip repeat is in `ab_dryrun2_results.json`.

|Metric (3 clips, 71 words)|CPU backend|PL backend (per-layer)|
|---|---:|---:|
|Final WER|11.27% (8)|11.27% (8)|
|Committed-preview WER|14.08% (10)|22.54% (16)|
|Previews served / merged|107 / 87|21 / 173|
|Preview compute p50 / p95 ms|230.5 / 320.9|1793.7 / 2243.9|
|Final compute p50 / max ms|641.6 / 853.7|4864.4 / 6960.9|
|L_update p50 / p95 / max ms (renderer, FIFO anchor)|749.7 / 6322.1 / 6659.8|3061.5 / 4596.4 / 6066.0|
|Mean board input power, W (idle 3.265)|3.907|3.386|
|Finals equal CPU vs PL|3/3|3/3|

This is a harness check, not a T1 result: three clips, old engine. WER on
three clips is not comparable to the 40-clip figures. The CPU L_update p95 is
inflated by an **output-thread stall**. On the CPU run of 1580-141084-0049,
which ran right after its PL run, inference finished in 51-67 ms, but
renderer completion took 2.3 s and then 6.0 s (`render_ms`); 38 updates then
drained late. The 2-clip repeat gave CPU L_update p50 655 ms with one 1.3 s
render outlier. Other runs render in ~10 ms. Suspects are SD-card
writeback while rewriting `caption.ppm`/the JSONL log, or the DRM
framebuffer write. Not yet isolated. PL power is lower because the A53
cores mostly wait on the engine during per-layer execution; the window covers
90% of the paced replay, sampled at about 0.1 s.

## Corrected replay implementation and new PL-inject batch

The following supersedes the inherited batch below. Run under the existing
`flock /home/ubuntu/stt/board.lock sh bench_board.sh`, native strict GCC build,
three CPU inference threads. This batch includes waveform VAD, PS regenerated
reflected finals, accumulated captions and output-stage timestamping (through
`ae89484`). Subsequent state/UI changes were host/native compiled and are used
in the separate continuous soak. The later renderer viewport adjustment is
host-build checked; it is not part of these timing measurements.

| Metric | Corrected paced PL-inject / backend CPU |
|---|---:|
| Clips / reference words | 40 / 848 |
| Final errors / WER | 39 / 4.5991% |
| Committed-preview errors / WER | 48 / 5.6604% |
| Renderer-completion L_update p50 / p95 / max | 620.740 / 771.817 / 1596.318 ms |
| L_word p50 / p95 / max | Not measured |
| Preview compute p50 / p95 | 276.084 / 323.284 ms |
| Final compute p50 / p95 | 761.145 / 833.735 ms |
| Served previews / finals | 1315 / 42 |
| Merged previews / lost stitch frames | 1224 / 0 |

Host scoring: `.venv/bin/python tests/app/analyze.py build/sw/app/measurements-corrected`.
Machine-readable summary: `results/app/corrected_results.json`. Raw timelines remain
ignored locally and under `~/stt/app/measurements`. The summary's `vad` field is
the inherited mel diagnostic, not the VAD actually selected by this replay.

L_update now includes the inference-to-output pipe and `cap_display` work. It
uses the FIFO occupancy probe during the first FFT, following `latency_i1.c`,
instead of process-start nominal injection time. Invalid/missed anchors are
excluded by the scorer. This is software renderer completion, not physical
scanout, TCP receipt, durable logging or completion of all output writes.
The probe's uncertainty bracket and detection of later paced-input gaps still
need exporting before full latency acceptance. These limitations apply even
though the measured renderer-completion p95 is below 1000 ms.

Held-out VAD: `.venv/bin/python sw/app/test_vad.py` passes on all 40 test clips:
33,793 blocks, zero waveform-active disagreements, 42 phrases and zero
begin/onset/finish disagreements against the actual Python policy functions.
This uses the fixed 0.01 RMS threshold and PCM16 quantisation, with no fitting.
There is no dev-clean calibration step because waveform RMS requires no mapping.
Live microphone mel VAD remains unvalidated. The combined VAD/final-feature
change reduces final errors from 40 to 39 and preview errors from 53 to 48;
this is not an isolated VAD WER ablation, and different merged ticks also
affect previews.

Replay finals now use phrase-local PCM, restart preemphasis, reflect both ends,
and apply utterance normalisation in `qnf_features`. They are a PS fallback,
not regenerated PL frames. Final WER remains 0.3538 percentage points above
the Python study's 4.2453%; feature-level equivalence has not been established.
Continuous PL preview framing and microphone final reflection remain open.

Direct phrase-final feature comparison (`.venv/bin/python
tests/app/check_final_features.py`) covers the same 42 PCM16 phrases and 1,996,736
valid feature values. RMS C/Python difference is 3.5083e-6; maximum absolute
difference is 4.7541e-4; maximum across the first/last two reflected frames is
3.2902e-5. These measure floating-point frontend differences, not bit equality
or proof of their effect on quantised network decisions. All 1,315 measured
preview updates had valid startup anchors (zero omitted for a missed probe).

New additive `fe_ui_poll` passes register simulation (`make -C sw/fedrv
check-tail check-ui`), preserving enable/source/pacing/IRQ controls and clearing
only the hardware-debounced sticky button bit. The application lights the LED
while Speaking and stops on a press. No physical press or LED observation was
performed. A read-only MMIO check after the final native smoke test returned
`STATUS=0x1`, sticky button bit 0 and LED control bit 0. The final native strict
build, C checks and paced PL replay passed with the committed viewport and EOF
changes, a valid FIFO anchor and zero lost stitch frames.
Listening/Speaking/Finalising/Committed events and accumulated
caption text are implemented; state-only events are excluded from scoring.

Open acceptance items: L_word with hypothesis frame times and the cached forced
alignment, full output-sink completion timing, capture-anchor continuity evidence,
Python feature/final parity, microphone VAD/reflection, physical button/display
validation. (The PL inference backend now exists; see I3 above.)

### Paced CPU-WAV comparison

All 40 clips completed under the board mutex, three CPU inference threads.
`tests/t1/bench_app.py --source cpu-wav --score-only --output
build/sw/app/measurements-cpu-paced/measurements-cpu-paced` scores the complete refreshed
download (the transfer helper nests a repeated directory download). The summary
is `results/app/cpu_paced_results.json`. Each board invocation uses the same WAV,
blob, prior and options as `bench_board.sh`, replacing the source with `cpu-wav`.

| Metric | Paced CPU-WAV / backend CPU |
|---|---:|
| Clips / reference words | 40 / 848 |
| Final errors / WER | 39 / 4.5991% |
| Committed-preview errors / WER | 50 / 5.8962% |
| Renderer-completion L_update p50 / p95 / max | 632.773 / 763.980 / 1698.237 ms |
| L_word p50 / p95 / max | Not measured |
| Preview compute p50 / p95 | 275.696 / 322.604 ms |
| Merged previews | 1218 |
| Valid / invalid preview latency samples | 1321 / 0 |

Final WER agrees between CPU-WAV and the corrected injection fallback. This
supports the final PCM regeneration path, while the Python 0.3538-point gap
remains unresolved. CPU-WAV timing uses the PS pacing clock; it does not have
a PL capture anchor. Both modes remain slower than the 120 ms preview hop.

### Ten-minute single-process continuous replay

`flock ~/stt/board.lock sh -c 'cd ~/stt/app; python3 soak.py'` passed: exit 0,
603.989 s elapsed for 600 s of looped PCM plus replay tail and final drain.
The native build includes state events and `fe_ui_poll`, before the later
viewport/EOF changes. RSS sampled once per second after 150 s ranged from
69,864 to 69,940 KiB (76 KiB spread), ending at 69,940 KiB. Warm-up includes
preloading the entire ten-minute WAV, runtime buffers and lazy page residency.
This is evidence of stability for this duration, not a leak proof or bounded
input-file memory guarantee. The maximum logged queue depth was 2 (sampled
after dequeue; the FIFO itself has eight slots), with zero lost stitch frames
and successful driver shutdown. Raw samples are ignored locally under
`build/sw/app/measurements-corrected/soak-summary.json` and on the board under
`~/stt/app/soak-summary.json`.

**Partial implementation; not system acceptance.** Native `sttcap` completed all
40 clips through paced PL injection with the CPU INT8 backend. Accuracy is in
the expected range. Latency below stops at inference completion and has a
nominal injection anchor, so it does not establish the proposal's display-time
L_update requirement. PL phrase framing and VAD differ from the reference.

## Configuration and reproduction

KV260, stt-fe loaded, existing u-dma-buf allocations; no firmware reload, reboot,
authentication change or key installation. Build:

```sh
make -C sw/app CFLAGS='-O2 -mcpu=cortex-a53 -std=c11 -Wall -Wextra -Werror -ffp-contract=off' all check
cd /home/ubuntu/stt/app
flock /home/ubuntu/stt/board.lock sh bench_board.sh
```

`bench_board.sh` uses the accepted CPU benchmark's 40 PCM16 mono WAVs under
`~/stt/cpu/clips`, `quartznet_int8.bin`, and `running_prior.json`. Backend CPU,
three threads, mel threshold 0.01, 0.8 s replay tail, generated selected policy
W=3 s/R=0.48 s/hop=120 ms/silence padding. Native application source is the
`25b6c31` implementation; subsequent test/report commits do not alter runtime.
The source files were compiled natively after explicitly removing transferred
host executables. Capture/output request core 0, qnrt requests cores 1–3.

All benchmark/timing and native smoke runs acquired `~/stt/board.lock`.
Board batch printed `COMPLETE` and returned zero. Raw JSONL/CSV/stdout remain in
`~/stt/app/measurements` and the local ignored `build/sw/app/measurements` directory.
The committed [machine-readable report](../../results/app/bench_results.json)
contains every clip's transcript and edit counts. Reproduce scoring:

```sh
python3 tests/app/analyze.py build/sw/app/measurements
```

## Forty-clip paced PL-inject / backend_cpu

| Metric | Result |
|---|---:|
| Clips / reference words | 40 / 848 |
| Final errors / WER | 40 / **4.7170%** |
| Committed-preview errors / WER | 53 / **6.2500%** |
| Update age at inference completion p50 / p95 | **593.703 / 769.741 ms** |
| Preview compute p50 / p95 | 273.222 / 321.686 ms |
| Final compute p50 / p95 | 757.169 / 802.050 ms |
| Served previews / finals | 1,308 / 43 |
| Merged previews / lost stitch frames | 1,199 / 0 |
| L_word | Not measured |

The three extra finals arise from the energy VAD splitting clips. This run is
not directly comparable to the Python 6.84% preview prediction: the PL framing,
VAD segmentation and served-tick subset differ. A lower preview WER does not
establish equivalence. This is a single batch, not a confidence interval or an
idle-verified performance acceptance run. No power/soak test was added here.

The update-age anchor is process CLOCK_MONOTONIC start plus nominal sample time,
not FIFO acceptance. First PL updates also include the lead-in interval. Output
queueing, renderer completion and TCP transmission are excluded. **Do not label
these numbers display-time L_update or use them for target acceptance.**

## VAD calibration

CSV records compare summed linear mel power with waveform RMS >=0.01 on the
nominal 160-sample block corresponding to the continuous PL frame index. There
are 33,737 comparisons. At runtime threshold 0.01, 3,946 disagree: **11.70%**.
The minimum-disagreement in-sample threshold is **0.002265476537445**, with
2,887 disagreements: **8.56%**. This fitted threshold has not been deployed or
evaluated on held-out clips. It is an analysis result, not a calibrated VAD
acceptance claim. Mel uses a preemphasised window rather than the reference's
nonoverlapping raw-waveform RMS block, so a scalar threshold cannot remove all
disagreement. Runtime exposes `--mel-threshold` for subsequent validation.

## Correctness and I/O evidence

Host WSL GCC and native A53 strict builds pass. The CPU_ONLY build also passes
without PL sources. C unit checks cover CTC repeats/blank/space trimming,
cross-window stitching, VAD lead-in, silence termination and boundary isolation.
`python3 sw/app/test_python.py` passes all three recorded network-logit fixtures
and 5,800 overlapping-window comparisons against the actual `stream_sim.py`
CTC function and the specified stitch contract.

Native offline CPU runs match every preview field checked against all three
supplied Python timelines:

| Clip | Actual / reference updates | Different updates |
|---|---:|---:|
| 1089-134691-0023 | 62 / 62 | 0 |
| 1188-133604-0025 | 59 / 59 | 0 |
| 121-127105-0003 | 63 / 63 | 0 |

Fields checked: phrase, feature_count, window_start, emitted, preview and
tentative; simulation times and onset-only state events are excluded.
Timelines are committed under `sw/app/` (the first is
`measurements-cpu.jsonl`). These runs use `--source cpu-wav --bulk`; the two
additional reference runs use four qnrt threads. This is content verification,
not a paced latency measurement.

`flock ~/stt/board.lock sh -c 'cd ~/stt/app; python3 smoke_board.py'` passes:
three TCP JSON events received, valid session log, PPM present, and SIGINT exits
zero within five seconds. The paced batch itself generated PPM files as root.
`/dev/fb0` exists; the renderer attempts supported 32-bpp framebuffer output.
No monitor was observed, so physical DisplayPort visibility is unverified.
The [rendered evidence](../../results/app/caption.png) was converted from the board's
PPM and visually inspected. ANSI tty fallback is implemented but not exercised
through SSH's redirected non-tty output.

## Remaining work

- Reconcile continuous PL frames with phrase-local restart/reflection and final
  feature regeneration. Current PL final normalises the captured phrase mel.
- Publish actual FIFO/sample-to-monotonic anchors and measure rendering/output
  completion; add L_word with censoring counts if alignment data is available.
- Validate a mel VAD threshold on held-out clips and compare phrase boundaries.
- Emit Listening and Finalising events, accumulate prior finals on screen, and
  flush open PL phrases at EOF. Speaking/Committed output already works.
- Integrate PMOD LED/button through a driver-owner command API. Existing fedrv
  forbids those calls concurrently with capture/injection; no API was modified.
- Test microphone register modes with actual hardware if that scope is added.
  Source selection exists for mic1/mic2/beam; no real microphone was used.
- PL inference backend remains an explicit ENOSYS stub. Its submit/poll contract
  accepts INT8 [64 x T] plus valid and returns [29 x T'] plus compute timing.

Only assigned application/tool/document paths were staged. Other workers' edits
were left untouched. Implementation was committed in working increments.
