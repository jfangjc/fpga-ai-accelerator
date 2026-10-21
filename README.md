# Moonshine on KV260

Experimental live speech-to-text with Moonshine Tiny/Small and one SPH0645LM4H-B.
Encoder feed-forward projections run on the FPGA; attention and decoding run on ARM.

## Requirements

KV260, ARM64 Ubuntu, Python 3.12, GCC, and the **existing provisioned hardware**:
`asr-v3-mic-select` at 100 MHz and the qualified bulk `qn_tile.ko` for kernel
`6.8.0-1036-xilinx`. Firmware/driver hashes are pinned in `config/`.
Supply these separately; this small repository includes accelerator RTL but
does not include the Vivado integration project, generated IP or firmware binaries.
Models download from Hugging Face at the revisions pinned in `software/moonshine/model.py`.

## Setup (on the board)

```sh
python3 -m venv .venv
.venv/bin/pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -r requirements.txt
sh scripts/build_moonshine.sh
sh scripts/build_moonshine_capture.sh
PYTHONPATH=software .venv/bin/python -m moonshine.ort_decoder --size tiny --out build/tiny-decoder
PYTHONPATH=software .venv/bin/python -m moonshine.ort_encoder --size tiny --out build/tiny-encoder
```

Exports require new output directories. For Small, use `--size small` and separate
artifact directories; `--accuracy` and `--low-latency` currently target Tiny.

## Run Tiny

Start from idle `k26-starter-kits` with no loaded `qn_tile` module. Replace the
driver path below. The wrapper verifies hardware hashes and restores the starting
FPGA app on exit. Use new log paths each run.

```sh
sudo env PYTHONPATH=software OMP_WAIT_POLICY=PASSIVE .venv/bin/python scripts/run_moonshine_board.py \
  --app asr-v3-mic-select --hardware-manifest config/hardware.json \
  --driver /path/to/qn_tile.ko --evidence reports/session.json -- \
  .venv/bin/python -m moonshine.live --size tiny --backend fpga --accuracy \
  --ort-decoder build/tiny-decoder --ort-encoder build/tiny-encoder \
  --mic-input primary --mic-channel right --vad-dbfs -50 --display text \
  --out reports/captions.jsonl
```

The custom carrier's J3 uses primary/right: WS **B10**, DATA **E12**, BCLK **D11**.
Microphone input applies 30 Hz DC removal. `audio-health` events report levels
and clipping. Tune `--vad-dbfs` and `--silence-ms` for the room.
For paced replay, replace the microphone options with `--wav clip.wav`; raw
microphone recordings also need `--highpass-hz 30`.

`--accuracy` uses overlapping two-second previews and up to 12 seconds of phrase
context for refined finals. `final-pending` is provisional; only commit `final`
events, in utterance order. Finals replace previews for their `utterance_id`.
Use `--low-latency` instead for the earlier three-second context profile.

The accuracy mode measured 0.99 s p95 preview update age and 2.24 s p95 final lag
on a 62.5-second paced test. Final lag excludes silence waiting. Recognition
accuracy on real conversation and 2x speedup over CPU remain unqualified.

## Checks

```sh
.venv/bin/python -m unittest discover -s tests
.venv/bin/python scripts/report_moonshine_latency.py reports/captions.jsonl
PYTHONPATH=software .venv/bin/python -m moonshine.accuracy --help
```

The accuracy tool accepts human transcripts and compares live finals or float/INT8
variants. `scripts/verify_moonshine_live.py` checks paced updates against the integer
oracle; `scripts/test_moonshine_physical.py` checks FPGA arithmetic through the wrapper.
Recordings, model files, binaries and local reports are excluded from Git.
