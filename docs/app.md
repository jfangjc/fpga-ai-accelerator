# sttcap application

## Independent final inference and phrase-indexed emission (2026-11-05)

For PL previews, `--final-backend auto|cpu|pl` selects final routing. The default
remains `auto`, selected by the completed phrase-indexed soak (preview p95
208.768 ms, zero merges). `auto`
retains PL RUN_NETWORK for padded lengths <=640 frames and routes longer
finals to a separate CPU INT8 backend. `cpu` routes all finals to that worker;
`pl` retains serial execution on the preview backend. `--final-threads 1..3`
defaults to three. Two threads inherit cores 2-3; three inherit cores 1-3,
with nice 5. Capture, frontend IRQ/injection, renderer and file writer retain
core-0 placement. Affinity and priority requests remain best effort.
CPU-preview mode retains its existing single CPU backend;
`--final-backend pl` requires `--backend pl`.

There is one submitter per backend, an eight-entry CPU final queue and a
32-slot copied-logit completion ring. The emitter now processes ready results
without waiting for an earlier phrase's unfinished final. Slots compact only
past emitted leading entries, retaining the same bounded backpressure. Each
outstanding phrase owns its preview stitch state and preceding served sample.
Previews of a phrase still retire in order. Final CTC is a separate decode:
it never supplies tokens to the next phrase's stitching. Retaining per-phrase
stitch state preserves every overlap/hold/emitted/lost-frame operation while
a late final cannot reset another phrase's state.

The Fig. 3 caption states apply per phrase. Speaking exposes the stable
preview plus an amber tentative suffix. Finalising retains that phrase's last
stable preview in amber as **tentative-committed**, dropping its unstable
suffix; later phrases remain live beside it. Committed replaces that phrase's
text in place with the final and turns it white. A later short phrase may
commit before an earlier long one. Lifecycle events never erase other text.
The framebuffer and asynchronous PPM use the same styled caption model.

JSONL, TCP and late-join snapshots include the same complete caption state:
`phrase` and `state` identify the changed phrase; `committed_prefix` contains
compacted leading finals; `prefix_before_phrase` identifies the first phrase
not represented by that prefix; `segments` lists the remaining phrases in
index order with `phrase`, `state`, `text` and `tentative`. A Finalising segment
is tentative-committed. `caption` joins the prefix and segment stable text.
Clients can replace segments by phrase index or reconstruct directly from
any event, including a snapshot, without assuming final-before-next-preview
arrival. Segment storage is bounded by 32 outstanding phrases; accumulated
leading finals retain their checked 64 KiB limit. Capacity errors fail the run.

`inference_finished` records backend completion. The legacy field name
`ordered_wait_ms` now measures ready-to-emission time without a cross-phrase
final barrier. `update_ms` remains measured at software framebuffer completion
(or the pre-TCP point when no usable framebuffer exists), so rendering and
output queueing remain included. Physical scanout and TCP receipt are excluded.
`--dump` still performs synchronous debug writes before completion-ready;
matched performance runs omit it. See [T2 results](results/t2.md#out-of-order-caption-emission)
for routing selection, final-latency investigation and acceptance evidence.

Forty-eight fixed 64x1600-byte input slots replace per-job quantised input
allocations; one 64x1600-float workspace belongs to capture. The CPU runtime
allocates its maximum activation buffers once, then runs a maximum-length
warm-up before the measured epoch. Output buffers and the completion/input
pools are pre-faulted. On glibc, one retained arena and an 8 MiB mmap threshold
keep the frontend's bounded temporary PCM/log-mel allocations resident; a
maximum-phrase feature warm-up occurs after persistent capture allocations,
before paced hardware starts. Whole-phrase features use `qnf_features_ws` with
a static pre-faulted 1.5 MiB scratch, and normalisation uses a static packed
copy, so finals make no heap allocation. The output is bit-identical to
`qnf_features`. The previous unsoaked `malloc_trim(0)`
edit was removed. WAV storage still scales with the full replay duration;
these bounds do not make arbitrary-duration WAV replay constant-memory.

## Soak follow-up (2026-11-05)

Paced injection retains the first-FFT FIFO acceptance anchor, then measures
hardware `FRAMES` transitions against `CLOCK_MONOTONIC` in an independent
observer. Narrow consecutive-edge brackets (at most 2 ms) are averaged into
one-second bins; a fixed 120-bin least-squares window updates the sample-to-time
mapping after ten bins. The initial edge calibrates the constant packet
processing delay against the FIFO anchor. This avoids assuming that PL pacing
and the Linux timekeeper have exactly the same rate. Until the fit is ready,
`clock_fitted=false` and `capture_anchor=fifo-first-fft`; thereafter the anchor
is `hardware-clock-fit`. CPU replay retains its PS pacing clock. Microphone
latency remains invalid because it has no FIFO acceptance anchor.

For short replays, `--sample-rate HZ` seeds the startup mapping from a recent
counter measurement (for example `--sample-rate 15999.625367592`). It also uses
that period in the first-FFT FIFO probe and packet-delay calibration. Such
events export `clock_calibrated=true`, `capture_anchor=fifo-measured-rate`;
the rolling fit still takes over after ten bins. The option is restricted to
`pl-inject`, accepts finite rates within 1,000 ppm of 16 kHz, and does not change
PS pacing or the hardware divider. Use a recent measurement because Linux
clock discipline changes the ratio over time. The 30-minute follow-up used
the default bootstrap; the optional seed was added afterward and checked by
strict native rebuild and a short board replay preserving every caption.
The observer was also hardened after the long run to initialise from an
actually read counter and bracket transitions inclusively from the preceding
MMIO-read start through the current read end. A further native 24-second
display/TCP/log/stage smoke check passes with the fit active and zero loss.

For each served update, `oldest_sample` names the preceding served audio
boundary (the original onset boundary for the first update), and `audio_time`
is that sample's mapped monotonic time relative to application epoch.
`update_ms` is framebuffer completion minus that timestamp, including capture,
inference and output queueing. `sample_rate`, `clock_fitted`, `inference_start`
and `queue_ms` make the mapping and inference wait auditable. The independent
observer never uses application dequeue times to estimate the clock, so a slow
consumer is not silently treated as a slower sample clock. `capture_clock`
stderr rows retain hardware counter/time pairs and bracket widths. The mapping
assumes continuous paced acceptance; gaps/stalls require separate investigation
using the capture error counters and clock residuals. Physical scanout and TCP
receipt remain outside this software completion metric.

`--stages` now streams all events through the low-priority writer, with no
per-run event array or 65,536-event cutoff. A fixed 64-slot, 256 KiB-per-line
pool replaces per-event log allocation; pages are touched at startup. A slot
is not reused until its JSONL, stdout and stage writes finish. PPM remains a
single latest-wins snapshot, queued atomically with its stage record; superseded
snapshots have zero PPM completion time. Writer exhaustion counts lost lines
and fails the invocation. WAV replay still holds the complete input in memory,
and the displayed accumulated transcript still has its checked 64 KiB limit.

Every preview replacement emits `merge_event` with the active request's final
flag and allocated feature length. The inference FIFO and final arithmetic
are unchanged. Long PL finals remain non-preemptible per-layer driver calls;
`tests/t2/analyze_t2.py soak` writes `attribution.json`, retaining each merge and
each update above 300 ms. Baseline cumulative logs cannot retrospectively
identify individual merge timestamps. Reproduction and measured results are
in [T2, Soak follow-up](results/t2.md#soak-follow-up).

## PL inference backend (I3 update, 2026-11-03)

`--backend pl --program QNP2` runs inference on the PL engine through
`sw/engdrv`; `--backend cpu` (default) keeps qnrt. The selected backend serves
previews; finals use the routing policy above. The PL build is the default non-`CPU_ONLY` build:
`ENGDRV=1` defines `HAVE_ENGDRV`, which excludes the ENOSYS `backend_pl` stub
in `backend.c`, compiles `sw/engdrv/backend_pl.c` and links
`build/sw/engdrv/libengdrv.a` (built by a sub-make) plus pthread. `ENGDRV=0` keeps
the stub (then `--backend pl` fails with ENOSYS; it never silently uses the
CPU). Board build, unchanged flags:

```sh
make -C sw/app CFLAGS='-O2 -mcpu=cortex-a53 -std=c11 -Wall -Wextra -Werror -ffp-contract=off' all check
```

Build definitions go in `DEFS`, so a command-line `CFLAGS` no longer drops
`-DCPU_ONLY`/`-DHAVE_ENGDRV`. The program must be exported from the same QNW1
as `--blob` (`model/qnc2_export.export`; on the board `~/stt/i2/program.qnp2`
matches `~/stt/cpu/quartznet_int8.bin`). The PL input scale is read from the
`--blob` header (float32 at byte 32), so PL and CPU quantise identically.

The driver picks the executor per request (`ENGDRV_MODE=auto|layer|network`,
default auto): one RUN_NETWORK command when the engine reports 0x00010002,
its TX buffer holds the program and the window is <= 640 frames; otherwise the
186-command RUN_LAYER graph, time-tiled beyond 800 frames, so finals up to the
15 s phrase cap (1504 frames) also run on the PL. The accepted stt-full150
bitstream reports 0x00010002; stderr prints the engine
version and mode at start. Each event records `backend` (`cpu`, `pl-network`,
`pl-layer`) and the engine `commands` count.

`--dump DIR` writes each final's exact INT8 input (`final-N.i8`, [64,T]) and
logits (`final-N.logits`, [29,stride]) plus `finals.tsv`. `i3_check.py RUN`
compares PL finals with `golden_int8.network` on those bytes, with the CPU
backend's finals/dumps, with the Python pipeline on the same phrase
segmentation, and with the TCP stream. `i3_board.sh STTCAP PROGRAM OUT CLIP...`
(under `flock ~/stt/board.lock`) produces those files for paced pl-inject
with both backends; `fb_check.py PREFIX...` compares the `/dev/fb0` capture with
the PPM. Results: [results/app.md](results/app.md), I3.

The renderer now packs any truecolour 16- or 32-bpp framebuffer layout; the
KV260 `xlnxdrmfb` is RGB565 (640x960 virtual, stride 1280), which the earlier
32-bpp-only path skipped.

## November 3 implementation update

The implementation now uses waveform RMS VAD for `pl-inject`, with the fixed
0.01 policy threshold and no fitted mel mapping. `test_vad.py` checks all 40
held-out clips against the Python phrase policy: 33,793 blocks and 42 phrases
agree exactly. Microphone input still uses the experimental mel threshold.

Replay finals regenerate features from the original PCM at the phrase boundary
using `qnf_features(..., running=0)`: phrase-local preemphasis, reflection at both
ends and whole-phrase normalisation. This is a PS replay fallback; continuous
PL preview features and microphone finals remain non-equivalent to the reference.

`time` records framebuffer completion (or the pre-TCP point without a usable
framebuffer), including output-pipe queueing. PPM runs asynchronously.
`inference_time` and `inference_update_ms`
preserve the earlier diagnostics. `update_ms` uses renderer time and the capture
anchor. Paced injection probes FIFO occupancy during the first FFT using the
I1 method; `latency_valid=false` if that interval is missed. CPU replay uses its
PS pacing clock. Bulk runs are excluded. This does not measure physical scanout,
TCP reception, log durability, or output-write completion. The follow-up clock
fit described above replaces nominal-rate extrapolation after startup.

The `caption` field and renderer preserve phrase-indexed pending finals and live
previews as described above. Listening, Speaking, Finalising and Committed events are emitted;
`event_only=true` marks state-only events, excluded from transcript/compute
scoring. Finalising is emitted when the final reaches the inference worker.
The accumulated transcript has a checked 64 KiB limit; reaching it fails
explicitly. ANSI styling still uses the per-phrase strings.

The additive `fe_ui_poll` API permits one UI owner alongside capture/injection.
The application lights the LED during Speaking and stops on a sticky hardware-
debounced button press. `make -C sw/fedrv check-ui` checks register behaviour.
Physical button/LED operation remains unverified. A press stops this process;
restart is a new invocation.

`tests/app/soak.py` generates ten minutes of looped PCM and monitors a single paced
process's RSS and queue depth. Invoke it under `flock ~/stt/board.lock` from
`~/stt/app`. L_word and microphone phrase reflection remain open. The sections
below describe the inherited implementation and should be read with these
corrections.

Status: CPU and PL caption pipelines have final-configuration board evidence in
[results/t2.md](results/t2.md). Continuous PL framing still differs from the
phrase-local Python reference, and physical microphone/display acceptance
remains outside that evidence.

## Build and run

From the repository root on Linux/WSL:

```sh
make -C sw/app check all
python3 sw/app/test_python.py
# A CPU-only executable, without fedrv/plio:
make -C sw/app clean
make -C sw/app CPU_ONLY=1 all
```

Native board flags:
`-O2 -mcpu=cortex-a53 -std=c11 -Wall -Wextra -Werror -ffp-contract=off`.
The Makefile compiles read-only dependencies directly; no dependency source or
library build output needs changing. `gen_policy.py` generates `policy.h` and
`policy.json` from `model/stream_policy.py`. Commit both generated files.

Board working directory is `/home/ubuntu/stt/app`; the source package preserves
the `sw/app`, `sw/qnrt`, `sw/frontend_c`, `sw/fedrv`, `sw/plio` relative layout.
Copy source files rather than host binaries. Remove `build/sw/app/sttcap` before native
compilation if a directory transfer included a host executable.

```sh
flock /home/ubuntu/stt/board.lock ../build/sw/app/sttcap \
  --source pl-inject ../cpu/clips/1089-134691-0023.wav \
  --blob ../cpu/quartznet_int8.bin --prior ../cpu/running_prior.json \
  --threads 3 --port 8765 --log session.jsonl --vad-log vad.csv
```

Sources: `cpu-wav FILE`, `pl-inject FILE`, `pl-mic`. Microphone selection:
`--mic 1`, `--mic 2`, `--mic beam`. WAVs must be PCM16, mono, 16 kHz.
Replay appends 0.8 s silence. `--bulk` serves every CPU preview; paced execution
uses latest-only preview replacement. PL bulk does not guarantee every preview:
the driver ring is bounded and capture can outpace application processing.
PL capture/injection uses `fe_stream_*` and checks stream errors/drops at join.
Microphone VAD (`pl-mic` only) sums the linear mel energy of bands 1–63. It
excludes band 0, which carries the MEMS microphone's DC offset. The noise floor
is tracked as the minimum of ten 50-frame block minima, covering 5 s. A frame
is active when its energy is at least `--vad-snr` dB above the floor
(default 8) and at least `--mel-threshold` (default 2e-6). The first 0.3 s is
ignored. `--mic-gain DB` (default 20) scales microphone band power before the
log. That is exactly a digital gain ahead of the FFT, and it moves the level
towards the running-normalisation prior. Replay keeps its waveform-RMS VAD.
`--vad-log FILE` records frame index, summed mel energy and waveform RMS
activity for injected clips, or frame index, band 1–63 energy, floor and
activity for the microphone. It writes behind a 1 MiB buffer. Mic-mode events
report `"audio_time":null` because there is no anchored sample clock. See
[mic results](results/mic.md).

## Threads and storage

Default core map: qnrt caller and its two worker threads are restricted to cores
1–3; application capture/control and output threads request core 0. With four
qnrt threads, the inference pool uses all four cores. Affinity failures are
nonfatal (for example, host cpusets). fedrv owns an additional IRQ capture thread
and injection thread; its API does not expose their affinity settings, so these
inherit the application capture thread's core-0 mask.

Capture performs VAD and causal feature processing while inference runs. CPU
capture uses `qnf_stream_*`, resetting to the dev prior at every phrase and
replaying the lead-in. Preview jobs own quantised windows. Eight jobs bound the
inference FIFO; adjacent pending previews for the same phrase replace each
other. Finals retain order and apply producer backpressure. Normalised features,
PL logs and CTC IDs are bounded by the phrase cap. WAV replay loads the input
clip into memory; this is not bounded independently of input length.

The dispatcher owns the preview backend, the optional final worker owns its
CPU backend, and the ordered emitter owns greedy CTC/stitch state. The output
thread consumes a bounded kernel pipe; slow output backpressures retirement
and eventually dispatch through the completion ring. SIGINT/SIGTERM stop
capture, discard pending capture-FIFO work and join already-dispatched work
and the output threads.
The tested TCP path uses nonblocking sends and disconnects a slow client after a
short write to avoid sending further events into a broken JSON stream.

## Numerics and backend

CPU front-end running Welford and final utterance normalisation reuse
`sw/frontend_c`; PL log/normalisation/quantisation reuse `sw/fedrv/features.c`.
Silence padding is 16 valid frames normalised with the fixed dev prior. Windows
begin at even feature indices; quantisation uses the blob input scale, float32
division, half-away rounding and saturation. CTC collapses repeats before
dropping blank 28 and preserves continuity across stitched windows.

`backend.h` specifies channel-major `[64][T]` signed features, padded T and valid
length, and `[29][stride]` signed logits with output valid length and compute
time. `submit` then `poll` is asynchronous for the PL backend (a driver worker
thread) and synchronous for the CPU. Result memory belongs to the backend until
the next submission. Without `HAVE_ENGDRV`, `backend_pl` fails with ENOSYS; it
never silently selects CPU.

## Outputs and measurements

JSONL is written to `--log` (default `session.jsonl` on the root filesystem).
TCP is enabled by `--port`; up to four clients are supported. Run
`python tools/caption_client.py BOARD --port 8765` for text or add `--json`.
Events contain phrase, Speaking/Committed state, preview, tentative, final,
capture sample time, feature/window/emitted indices, inference time, queue depth,
cumulative preview merges and lost stitch frames.

The renderer embeds a 5x7 font scaled 2x into a 640x240 RGB buffer. Committed
text is white, tentative suffix amber. It always overwrites `caption.ppm` and
attempts truecolour 16/32-bpp `/dev/fb0` output respecting stride and channel offsets.
ANSI output is enabled when stderr is a terminal. The screen presents the newest
part of accumulated finals plus the current preview. Framebuffer availability does not prove a monitor
displayed it; no physical display acceptance is claimed.

`time` and `update_ms` timestamp software framebuffer completion; the capture
clock and precise oldest-new-audio definition are described above. Bulk and
`latency_valid=false` samples must never enter latency percentiles. L_word is
derived by the T2 analysis harness using alignment and renderer timestamps;
see [results/t2.md](results/t2.md#l_word-and-the-preview-wer-gap).

## Harness

`tests/t1/bench_app.py` is a Windows PC collector. It invokes `tools/board.ps1`,
never reads credentials, and holds the board flock for each run. `--prepare`
uses soundfile to convert/copy the 40 FLACs; default board paths reuse the
accepted CPU benchmark's WAVs. `--source cpu-wav --bulk` is the offline check;
`--source pl-inject` is the paced experiment. `--backend pl` (with
`--program`) selects the PL engine; `--ab` is the paired T1 mode (each clip on
CPU and PL back to back, order alternating per clip, both summaries plus
per-clip pairs in `summary.json`); `--power` runs `~/stt/power.sh` in
parallel over 90% of each paced replay plus a 50-sample idle baseline;
`--board-dir`/`--sttcap`/`--clips` select the board directory, binary and
clips. `--score-only` scores downloaded
timelines without accessing the board. The transcript scoring uses corpus-level
word edit counts for final and committed preview WER. Offline reference
comparison checks every update's text and indices; it excludes simulation time.

`tests/app/bench_board.sh` runs the same 40 board WAVs in a single locked batch.
`tests/app/analyze.py DIRECTORY` scores the resulting JSONL and VAD CSV files and
fits an in-sample energy threshold. A fitted threshold is not a validation score.
`tests/app/check_reference.py TIMELINE...` checks offline CPU previews against the
three supplied reference timelines. `smoke_board.py` checks TCP, logging, PPM and
SIGINT; run it in the board application directory under the flock.

## Integration work remaining

Continuous PL framing differs from the reference's phrase-local restart,
preemphasis and reflection. Replay finals regenerate phrase-centred features
from retained PCM; microphone finals normalise captured phrase mel.
The energy VAD cannot exactly reproduce arbitrary waveform RMS
from mel power alone; report calibration disagreement explicitly. Current PL
preview count/lattice therefore cannot claim reference equality.

Listening/Finalising events, accumulated caption display, replay EOF finalisation
and the additive concurrent-safe LED/button API are implemented. Physical
button/LED observation, monitor scanout and microphone acoustic validation
remain open. PL graph preemption remains unimplemented; independent CPU
routing avoids that driver requirement while retaining ordered-display waits.
