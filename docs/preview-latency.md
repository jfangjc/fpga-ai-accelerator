# Tiny hybrid previews on KV260

The experimental `--provisional-preview` profile uses 250 ms updates, a
one-second preview window, and 0.5 seconds of preview overlap. It recomputes
unfinished encoder frames on every update and discards drafts when their audio
window moves. Only committed features enter the memory used for final
refinement. Preview text remains revisable.

Encoder feed-forward projections execute on the physical FPGA at 100 MHz.
Attention and decoding use the existing INT8 ONNX artifacts on ARM, with four
native threads and two Torch threads. No new bitstream or decoder offload is
required. Normal `--accuracy` behavior remains unchanged.

The latency target is **p95 oldest-update age <= 500 ms** on paced replay. That
metric includes acquisition, update audio duration, computation, and required
encoder lookahead. Provisional previews do not wait for the 240 ms encoder
lookahead. This metric does not establish aligned word latency or a worst-case
latency guarantee.

Final captions retain the full phrase context and the 700 ms silence endpoint.
Reported final source-end lag excludes that endpoint waiting time. Final WER
and provisional preview accuracy are different measurements.

Measured on 22 October 2026, using the selected profile's CLI defaults:

| Paced fixture | Original accuracy p95 preview age | Provisional p95 preview age | Worst provisional age | Final word errors |
| --- | ---: | ---: | ---: | ---: |
| Sustained, 62.5 seconds | 987 ms | 480 ms | 604 ms | No reference |
| Synthetic conversation 1 | 946 ms | 469 ms | 504 ms | 0 / 35 |
| Synthetic conversation 2 | 936 ms | 476 ms | 526 ms | 1 / 34 |

Both synthetic conversation finals matched the original mode's error counts:
1 error across 69 reference words (1.45% WER). These two synthetic clips do not
qualify real conversation accuracy or provisional word accuracy. Many preview
updates reach their partial token budget (206/230, 38/42, and 38/45 respectively);
final refinement completed every utterance without a token-limit failure.

Final source-end lag p95 was 2.51 seconds on sustained speech and 766/798 ms on
the conversation clips, before adding silence endpoint waiting. The sustained
test concurrently captured 65 seconds from the primary/right microphone:
1,040,000 nonzero samples, zero reported drops, and 20 ms capture queue high
water. All three inference logs recorded physical FPGA executions. The wrapper
restored `k26-starter-kits` after the session.

All 28 repository regression tests passed on the board, including native
transport callbacks and float-encoder provisional-tail equivalence. Raw timing,
accuracy, capture, and restoration evidence is saved locally in
`reports/provisional-preview-20261022/` and on the board in
`reports/local/qualification250/`.

Post-run integer-oracle verification matched all 351 saved updates and all 17
finals across the three fixtures, with zero token or frame-count mismatches.
The deployed runtime source hash was
`9bdbb1a365834c6d229a8c513346667200d75cfc1d7fb863a0f8b45c7089f123`.
`reports/provisional-preview-20261022/summary.json` aggregates the checked
latency, WER, regression, capture, restoration, and oracle evidence.

A subsequent CPU comparison used the same provisional profile, source audio,
filtering, segmentation, Torch/native thread counts, and ONNX attention/decoder
artifacts. CPU inference used QNNPACK INT8 encoder feed-forward layers and
recorded no FPGA inference executions. Both sustained tests included concurrent
65-second microphone capture.

| Paced fixture | CPU INT8 p95 preview age | Hybrid p95 preview age |
| --- | ---: | ---: |
| Sustained, 62.5 seconds | 475 ms | 480 ms |
| Synthetic conversation 1 | 525 ms | 469 ms |
| Synthetic conversation 2 | 523 ms | 476 ms |

The hybrid path reduced preview p95 by 9-11% on the two conversation samples in
these runs. Sustained preview latency was essentially tied. CPU final source-end
lag p95 was 2.21 seconds versus 2.51 seconds hybrid on sustained speech; the
conversation finals were nearly identical (CPU 759/793 ms, hybrid 766/798 ms).
Both paths had 1 final word error across the same 69 synthetic reference words.

These are single current-profile runs per backend per fixture, with no separate
inference warmup. Short-clip p95 is sensitive to a few slow updates. The CPU and
FPGA FFNs also use different INT8 quantization arithmetic. This establishes a
modest practical latency difference on these fixtures. Both backends benefit
from the new streaming profile. Earlier
repeated trials of the original accuracy profile measured CPU 973 ms versus
hybrid 987 ms p95 preview age.

CPU comparison evidence is in `reports/cpu-preview-comparison-20261022/`, with
matched-setting checks and processing-time distributions in `comparison.json`.
The benchmark helper accepts `--backend cpu-int8` for CPU inference and
`--backend fpga` for hybrid inference; use separate new output directories.

The board installation is `/home/ubuntu/moonshine-preview-20261022`. It uses the
existing virtual environment, native library, model artifacts, and qualified
firmware through symlinks to `/home/ubuntu/moonshine-kv260-20261020`. The earlier
deployment is retained.

Start microphone captions on the board with new evidence and output filenames:

```sh
cd /home/ubuntu/moonshine-preview-20261022
sudo env PYTHONPATH=software OMP_WAIT_POLICY=PASSIVE \
  HF_HUB_CACHE=/home/ubuntu/.cache/huggingface/hub HF_HUB_OFFLINE=1 \
  .venv/bin/python scripts/run_moonshine_board.py \
  --app asr-v3-mic-select \
  --hardware-manifest build/streaming-firmware/manifest.json \
  --driver /home/ubuntu/kv260-asr-mvp-20261015/drivers/prebuilt/6.8.0-1036-xilinx/bulk/qn_tile.ko \
  --evidence reports/local/live-preview-session.json -- \
  .venv/bin/python -m moonshine.live --size tiny --backend fpga \
  --provisional-preview --ort-decoder build/tiny-decoder-v2 \
  --ort-encoder build/tiny-encoder-v1 --mic-input primary --mic-channel right \
  --vad-dbfs -50 --display text --out reports/local/live-preview.jsonl
```

The wrapper checks firmware and driver hashes and restores the idle FPGA app on
exit. `final-pending` events remain provisional; commit only `final` events and
replace earlier text for the same utterance ID.

For a paired paced comparison, use `scripts/benchmark_moonshine_preview.py`
inside the same wrapper. It compares the original two-second/400 ms committed
profile with the selected one-second/250 ms provisional profile and applies
30 Hz DC removal to both. Use `scripts/verify_moonshine_live.py` after timing to
check the exact saved update boundaries against the integer oracle.
