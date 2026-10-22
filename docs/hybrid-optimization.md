# Exact software optimizations for Tiny hybrid inference

The optimization workspace is `/home/ubuntu/moonshine-opt-20261022` on the
KV260. The previously qualified deployment is retained at
`/home/ubuntu/moonshine-preview-20261022`. These changes use the existing
100 MHz FPGA image and driver.

Detailed profiling of eight seconds of speech, using 32 fixed preview updates,
recorded 20 ms frontend, 71 ms encoder, and 86 ms decoder work per update.
One-token calls accounted for 84% of decoder step time. Cache cropping consumed
only 2.3 ms across the entire probe, so cache-copy optimization was not selected.
Encoder projection quantization and float output restoration together cost
about 13 ms per preview.

The new `--native-projections` option replaces those NumPy host transforms with
native ARM/NEON helpers. The whole-projection input scale, nearest-even rounding,
INT32 accumulation across K tiles, output scaling order, bias addition, and
output layout are preserved. FPGA arithmetic and weight loading are unchanged.
The helpers are tested against the NumPy reference, including bias, rounding
ties, odd dimensions, zeros, nonfinite inputs, and multi-tile projections.

The optional single-token decoder graph fixes the query length to one and
removes the zero causal-mask additions. All cached keys and the current key are
causal for one query. Prefill retains the original graph and mask. No decoder
cache contents survive an encoder-memory update. Original graph files and
weights are copied unchanged, and all runtime artifacts are hash checked.

Two reversed-order runs per configuration produced this computation-only
comparison. Every variant preserved the same preview token sequences:

| Configuration | Mean computation across two runs | Per-run computation p95 range |
| --- | ---: | ---: |
| Original host transforms and decoder | 179.4 ms | 214.4-219.4 ms |
| Native host transforms only | 171.3 ms | 208.7-208.8 ms |
| Single-token decoder only | 174.5 ms | 209.6-211.3 ms |
| Both optimizations | 167.8 ms | 204.4-204.7 ms |

The combined reduction was about 6.5% in this probe. Four native decoder threads
remained fastest in the separate one-token benchmark. Host thread tuning did
not justify changing the existing two-thread setting. Computation p95 remains
above the proposed 150 ms goal, so the 250 ms acquisition interval is retained.
These figures are computation measurements, not end-to-end preview age.

The transport profile separates weight-load wall time, run wall time, driver
exchange time, packing, and unpacking. These times overlap and must not be added
as independent costs. Raw bulk transactions do not report hardware cycle/stall
counters; zero cycle fields do not establish zero stalls.

For a new checkout, build a separate library and create specialized artifacts
in a new directory:

```sh
sh scripts/build_moonshine.sh build/native-opt/libqnc_moonshine.so
PYTHONPATH=software .venv/bin/python scripts/specialize_moonshine_decoder.py \
  build/tiny-decoder-v2 --out build/tiny-decoder-specialized
```

The deployed optimization workspace already contains both outputs. The
specialization command refuses an existing output directory. Export checks
covered zero/nonzero decoder caches, five context lengths, float and INT8
graphs, matching token choices, and matching cache tensors. INT8 logits were
identical in all five tensor cases.

Use the existing board wrapper with `--provisional-preview`, adding
`--native-projections --library build/native-opt/libqnc_moonshine.so` and
`--ort-decoder build/tiny-decoder-specialized`. Keep
`--ort-encoder build/tiny-encoder-v1 --threads 2 --native-threads 4`.
The microphone remains primary/right. Finals retain full phrase context and
the 700 ms silence endpoint.

Start the optimized microphone path with fresh evidence and event filenames:

```sh
cd /home/ubuntu/moonshine-opt-20261022
sudo env PYTHONPATH=software OMP_WAIT_POLICY=PASSIVE \
  HF_HUB_CACHE=/home/ubuntu/.cache/huggingface/hub HF_HUB_OFFLINE=1 \
  .venv/bin/python scripts/run_moonshine_board.py \
  --app asr-v3-mic-select \
  --hardware-manifest build/streaming-firmware/manifest.json \
  --driver /home/ubuntu/kv260-asr-mvp-20261015/drivers/prebuilt/6.8.0-1036-xilinx/bulk/qn_tile.ko \
  --evidence reports/local/optimized-mic-session.json -- \
  .venv/bin/python -m moonshine.live --size tiny --backend fpga \
  --provisional-preview --native-projections \
  --library build/native-opt/libqnc_moonshine.so \
  --ort-decoder build/tiny-decoder-specialized \
  --ort-encoder build/tiny-encoder-v1 --threads 2 --native-threads 4 \
  --mic-input primary --mic-channel right --vad-dbfs -50 --display text \
  --out reports/local/optimized-mic.jsonl
```

For CPU comparisons, select `--backend cpu-int8` and the same specialized
decoder, omitting `--native-projections`. The benchmark helper supports these
options and separate output directories. CPU encoder FFNs use QNNPACK INT8;
FPGA encoder FFNs retain their existing quantization arithmetic.

Nine paced replays then compared the original hybrid, optimized hybrid, and
CPU INT8 paths. All used the same source audio, 250 ms updates, provisional
one-second previews with 0.5-second overlap, two host threads, four native
threads, and 30 Hz DC removal. CPU used the same specialized decoder as the
optimized hybrid.

| Recording | Original hybrid p95 age | Optimized hybrid p95 age | CPU INT8 p95 age | Hybrid mean compute, original → optimized |
| --- | ---: | ---: | ---: | ---: |
| Sustained speech, 62.5 s | 475.9 ms | 472.7 ms | 462.3 ms | 173.5 → 165.7 ms |
| Synthetic conversation 1 | 463.0 ms | 454.0 ms | 463.4 ms | 166.5 → 154.3 ms |
| Synthetic conversation 2 | 478.7 ms | 464.1 ms | 472.3 ms | 168.6 → 156.9 ms |

These are single runs per configuration, with order varied across recordings;
small differences remain subject to run-to-run variation. Age is measured from
the oldest audio in an update through its preview computation. It is not an
aligned word latency measurement. The optimized path meets the 500 ms p95
target on these fixtures, with a 4.5–7.3% reduction in mean preview computation.
Its computation p95 was 186–210 ms, so a 200 ms update interval is not enabled.
Worst preview age was 585 ms on sustained speech and 476/512 ms on the two
conversations; 500 ms is not a hard ceiling.

All three variants produced one final word error across 69 synthetic reference
words. Real human conversation accuracy remains unqualified. Concurrent
65-second microphone capture on each sustained run delivered 1,040,000 samples
with zero reported drops. The board wrapper restored `k26-starter-kits` after
qualification. Raw events, timing reports, microphone diagnostics, physical
conformance, and comparison results are in
`reports/hybrid-optimization-20261022/qualification` locally and
`reports/local/qualification` in the optimization workspace on the board.

Verification passed all 32 regression tests, four additional scalar helper
tests, physical raw/tiled projection conformance, and all 351 recorded optimized
updates against the integer oracle, including 17 finals. There were no token or
frame-count mismatches. Rebuilding with the documented build command produced
the identical qualified library. `reports/hybrid-optimization-20261022/summary.json`
aggregates the matched-setting checks and verification evidence.
