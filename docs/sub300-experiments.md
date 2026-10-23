# KV260 experiments for a 300 ms preview target

The board workspace for the retained implementation is
`/home/ubuntu/moonshine-fpga-20261023`. Frontend-offload experiments are retained
separately in `/home/ubuntu/moonshine-front-20261023`.
The existing optimized deployment is retained. These experiments use the
qualified 100 MHz image and bulk driver; no new bitstream is installed.

## Measured result and tradeoffs

The retained profile measured the following on 23 October 2026. All recordings
used the same pinned Tiny model, specialized decoder, native encoder artifacts,
two Torch threads, four native threads, and 30 Hz DC removal.

| Recording | Previous optimized hybrid p95 | New hybrid p95 | CPU p95, new streaming settings |
| --- | ---: | ---: | ---: |
| Sustained speech, 62.5 s | 473 ms | **267 ms** | 267 ms |
| Conversation 1 | 454 ms | **264 ms** | 251 ms |
| Conversation 2 | 464 ms | **257 ms** | 444 ms |

The principal reduction comes from 120 ms updates and 400 ms preview context,
compared with 250 ms updates and one-second context. This does not establish a
large FPGA speedup over CPU. These are single qualification runs per case and
backend; short-clip p95 is sensitive to slow updates. The profile was selected
on these fixtures, without a held-out latency or human-conversation test set.

Final recognition retained zero errors on Conversation 1 and one on
Conversation 2: **1/69 reference words**, matching the previous profile.
Shorter preview recognition itself remains unqualified. On sustained speech,
both backends concurrently captured 1,040,000 primary/right microphone samples
over 65 seconds with zero reported drops.

The faster preview cadence leaves less time for cooperative final refinement:

| Recording | Previous optimized final lag p95 | New hybrid final lag p95 | Worst new hybrid preview age |
| --- | ---: | ---: | ---: |
| Sustained | 2.09 s | **8.62 s** | 567 ms |
| Conversation 1 | 600 ms | **1,794 ms** | 473 ms |
| Conversation 2 | 735 ms | **1,652 ms** | 517 ms |

Final lag excludes the 700 ms silence endpoint. This is an opt-in preview-latency
experiment; the default profile is retained. It is unsuitable when prompt final
captions are the priority. Source logs, physical checks, and CPU comparisons
are in `reports/fpga-latency-20261023/` locally and `reports/qualification/` in
the retained board workspace.

## FPGA host path

The native library now accumulates K-tile INT32 results directly while
unpacking FPGA responses. The first K tile overwrites its output slice and
later tiles add to it. This removes temporary partial-result arrays, their
NumPy additions, and initialization of the final accumulation buffer.
The Python projection bounds the complete dot product before selecting this
INT32 path; larger generic projections retain the INT64 reference path.
Older libraries continue to use the previous implementation.

ARM/NEON input quantization now packs and transposes 16-by-16 tiles rather
than scattering each quantized byte separately. Whole-projection scaling,
nearest-even rounding, bias, FPGA operands, and restoration order are retained.
Scalar and NEON implementations have the same arithmetic contract.

Native input-quantization microbenchmarks measured 7–16% lower median helper
time across six model-sized shapes. Four whole-preview AB probes preserved all
32 update token sequences, but did not establish a repeatable overall compute
speedup: mean computation was 164.1/166.8 ms with the old library and
170.8/162.4 ms with the new one. The acquisition/context change accounts for
most of the end-to-end result.

These changes target host overhead around physical FPGA inference. They do
not make the decoder run on the FPGA. In the prior saved eight-second probe,
load/run wall time was about 20 ms per update and decoder work about 86 ms.
Those measurements overlap projection timings and must not be summed twice.
Eliminating FPGA load/run time alone cannot reach 300 ms with a 250 ms
acquisition interval and the remaining CPU work.

An additional experiment lowered both frontend convolutions to FPGA INT8
projections. With 120 ms updates this regressed Conversation 1 from zero final
word errors to three. That implementation was rejected. The retained path
uses the original ARM float convolutions and FPGA encoder FFNs.

Disabling speculative draft verification did not improve the short-window
probe. A soft final-refinement scheduling guard also failed its paced target.
Both experiments were discarded.

## Experimental streaming settings

The experimental profile uses 120 ms updates, 400 ms previews, and 200 ms overlap:

```sh
--provisional-preview --chunk-ms 120 \
--preview-seconds .4 --preview-overlap-seconds .2 \
--native-projections --library build/direct/libqnc_moonshine.so
```

This reduces the previous one-second preview context to 400 ms and is a recognition tradeoff.
Preview hypotheses remain revisable. Full-context final refinement and the
700 ms silence endpoint retain their previous settings. Final WER alone
cannot qualify the accuracy or usefulness of these shorter previews.

The target is strictly below 300 ms p95 **oldest-update age** on paced replay,
including acquisition and queued work. It is not aligned word latency or a
worst-case guarantee. Speech onset retains 300 ms pre-roll, so some initial
updates necessarily exceed this target. Timings from an unpaced computation
probe cannot establish that the paced target has been met.

The benchmark helper accepts `--target-p95-ms 300` and reports computation
p95 and the number of updates whose computation exceeds their configured
interval. The latency target is still checked against the complete paced age.
Its default target remains 500 ms.

## Reproduce

Build a separate library:

```sh
sh scripts/build_moonshine.sh build/direct/libqnc_moonshine.so
```

Inside the existing board wrapper, run the benchmark with the pinned artifacts
and a fresh output directory:

```sh
.venv/bin/python scripts/benchmark_moonshine_preview.py \
  --wav build/conversation-accuracy-v1/conversation-2.wav \
  --ort-decoder build/tiny-decoder-specialized \
  --ort-encoder build/tiny-encoder-v1 \
  --backend fpga --native-projections \
  --library build/direct/libqnc_moonshine.so \
  --provisional-only --chunk-ms 120 \
  --preview-seconds .4 --preview-overlap-seconds .2 \
  --target-p95-ms 300 --out reports/new-sub300-comparison
```

Use the same arguments with `--backend cpu-int8`, omitting
`--native-projections`, for a CPU comparison with the same
streaming settings. CPU and FPGA quantization arithmetic differs. Use a separate output
directory. The wrapper remains responsible for hash verification, exclusive
hardware access, and restoration of the starting FPGA application.

## Verification

All 33 regression tests passed with the new ARM native library. Six native-host
and transport tests also passed in a separate scalar build. Callback transport
tests cover overwrite and accumulation across packet boundaries, odd dimensions,
and modulo-INT32 overflow; they simulate the protocol. Separate physical tests
checked raw FPGA arithmetic, prepared-weight reuse, caller-owned output buffers,
and a projection spanning both output and K tiles.

All **702 saved events**, including **17 finals**, then matched the independent
integer oracle, with zero token or frame-count mismatches. Rebuilding the native
library with the documented script produced the identical SHA-256:

```text
5e071d60eb6d69c49610138c3e9a30b77228eb6f1d08007ed000fabc22d6d027
```

The recorded runtime source hash matches this checkout:

```text
3972a77e3a5430be6568749837b62121a70ba60dbe1b020cf969975eb47b297f
```

`reports/fpga-latency-20261023/summary.json` aggregates source and library hashes,
matched CPU settings, accuracy, physical execution, regression results, capture
diagnostics, oracle comparisons, and hardware restoration. All checks passed.
The board wrapper restored `k26-starter-kits` and unloaded `qn_tile`.

Run the native regressions on the board with:

```sh
MOONSHINE_NATIVE_LIBRARY=build/direct/libqnc_moonshine.so \
  .venv/bin/python -m unittest discover -s tests
```

For live microphone use on the prepared board workspace, choose fresh log names:

```sh
cd /home/ubuntu/moonshine-fpga-20261023
sudo env PYTHONPATH=software OMP_WAIT_POLICY=PASSIVE \
  HF_HUB_CACHE=/home/ubuntu/.cache/huggingface/hub HF_HUB_OFFLINE=1 \
  .venv/bin/python scripts/run_moonshine_board.py \
  --app asr-v3-mic-select \
  --hardware-manifest build/streaming-firmware/manifest.json \
  --driver /home/ubuntu/kv260-asr-mvp-20261015/drivers/prebuilt/6.8.0-1036-xilinx/bulk/qn_tile.ko \
  --evidence reports/new-fast-mic-session.json -- \
  .venv/bin/python -m moonshine.live --size tiny --backend fpga \
  --provisional-preview --chunk-ms 120 \
  --preview-seconds .4 --preview-overlap-seconds .2 \
  --native-projections --library build/direct/libqnc_moonshine.so \
  --ort-decoder build/tiny-decoder-specialized \
  --ort-encoder build/tiny-encoder-v1 --threads 2 --native-threads 4 \
  --mic-input primary --mic-channel right --vad-dbfs -50 --display text \
  --out reports/new-fast-mic.jsonl
```
