# Live FPGA-accelerated speech-to-text on the Kria KV260

A live captioning device on the AMD Kria KV260. A PMOD I2S MEMS microphone
feeds a speech front end in the programmable logic (PL). A custom INT8
accelerator, also in the PL, runs the QuartzNet 15x5 acoustic model. The Arm
A53 cores do voice activity detection, CTC decoding and the caption display.
Captions appear on a DisplayPort monitor and are streamed over TCP to PCs on
the network. All processing stays on the board.

## Results

Final configuration: the `stt-full150` overlay. The front end runs at 100 MHz
and the engine at 150 MHz. Audio is injected after the I2S FIFO, over 40
LibriSpeech test-clean clips (848 words).

| Metric | Measured | Target (core / stretch) |
|---|---|---|
| Speed-up vs the same INT8 model on the A53s | **3.14x** (3 s window: 69.6 ms PL vs 218.4 ms CPU) | 2x / 3x |
| Preview latency p95 | **205 ms** | 1000 ms / 250 ms |
| Final WER | **4.60 %** (float 4.36 %) | 10 % / float + 1 point |
| Board power while captioning | **3.68 W mean**, 4.60 W max | 20 W / 15 W |
| A53 load of the captioning app | **10.7 %** of one core (274.6 % with the CPU backend) | reported |

With the live microphone (PC speaker to microphone), the final WER is 11.2 %.
A 30-minute soak dropped no samples. See [docs/results.md](docs/results.md)
for every metric and its evidence. The project's reach goal, Moonshine
Streaming Tiny on the same engine, is kept in a separate `moonshine`
repository; this one covers QuartzNet only.

## Architecture

```
MEMS mic --I2S--> [PL, 100 MHz] I2S rx -> FIFO -> beamformer -> pre-emphasis/FFT/log-mel
                                                                   | DMA
                                                                   v
                  [PS, A53] VAD -> 3 s windows every 120 ms -> engine driver
                                                                   | DMA
                                                                   v
                  [PL, 150 MHz] INT8 engine: 32x16 MAC array, 16x16 depthwise unit,
                                requantiser, residual add, on-chip RUN_NETWORK
                                                                   | logits
                                                                   v
                  [PS, A53] greedy CTC, stitching, caption states -> DisplayPort, TCP, SD log
```

Utilisation: 68.6k LUT (59 %), 843 DSP (68 %), 60 URAM (94 %),
74.5 BRAM tiles (52 %).

## Repository layout

Each top-level folder has one job:

| Path | Contents |
|---|---|
| `rtl/` | SystemVerilog: I2S receiver, beamformer, FFT/mel front end (`rtl/frontend`); INT8 engine and QNC2 command parser (`rtl/engine`); KV260 pin constraints |
| `sim/` | Verilator testbenches and their golden-vector generators (`make check` in each folder) |
| `hw/` | Vivado build: block-design Tcl, device-tree overlays, build and packaging scripts |
| `sw/` | board software with its unit tests: `app` (`sttcap`), `qnrt` (CPU INT8 runtime), `engdrv`, `fedrv` and `plio` (drivers), `frontend_c` |
| `model/` | Python: QuartzNet float and INT8 golden models, quantisation, export, streaming simulation, WER |
| `product/` | appliance packaging: systemd service, control script, installer |
| `tools/` | everyday PC tools: board access, deploy, release and install, caption clients |
| `tests/` | board and integration test harnesses, one folder per test; see [tests/README.md](tests/README.md) |
| `results/` | measured evidence, one folder per test, described by the reports in `docs/results/` |
| `docs/` | specifications, design notes, results reports, setup guide, original proposal |

## Building and running

Everything except simulation runs natively on Windows. You need:
- Python 3.14 (`winget install Python.Python.3.14`, then open a new terminal);
- Vivado/Vitis 2026.1, which also provides `bootgen` and `dtc`;
- PuTTY's `plink` and `pscp` on `PATH`, for board access.

Linux works for the Python and simulation steps; there, use `.venv/bin/python`
where Windows uses `.venv\Scripts\python`. The board runs Ubuntu 24.04 for
the KV260 and compiles all of its C code itself during the install.

1. **Model and golden vectors.** Follow [model/README.md](model/README.md).
   Datasets, weights and generated vectors go into the ignored `data/`
   directory and are not part of this repository.
2. **Simulation (Linux or WSL only; optional).** It needs Verilator, gcc and
   make. Run `python sim/frontend/extract_speech.py` once to take three speech
   clips from the downloaded dev-clean archive. Then run `make -C sim/frontend
   check` and `make -C sim/engine check`. Both use the `.venv` from step 1,
   created on Linux.
3. **Hardware.** Run `./hw/build.ps1 full150` and
   `./tools/deploy.ps1 full150`; see [docs/platform.md](docs/platform.md).
   Board access uses `tools/board.ps1`, which reads the host, user, password
   and host key from an ignored `.board.env` file.
4. **Appliance.** Run `tools/product_install.ps1`. It builds a release
   tarball and installs the `stt-caption` service on the board; see
   [docs/product.md](docs/product.md). To set up a new board from a blank SD
   card, see [docs/setup_kv260_ubuntu24.md](docs/setup_kv260_ubuntu24.md).
5. **Captions on a PC.** Run `python tools/caption_live.py <board-ip>`.
   To replay a recorded session without a board, run
   `python tools/caption_live.py --replay results/mic/t40/session.jsonl.gz --speed 2`.

The model step (step 1) and the Vivado build (step 3) are independent, so you
can run them at the same time. The model step needs about 1.5 GB of downloads.

Every build product goes to `build/`, which mirrors the source tree:
`build/sw/app/sttcap`, `build/sim/engine/obj_engine/`, `build/hw/full150/`
and `build/release/`. Deleting `build/` cleans everything, and `make clean` in
a folder removes that folder's part. Make honours `BUILD_ROOT=DIR` to build
elsewhere. To keep Python caches there too, set
`PYTHONPYCACHEPREFIX=$PWD/build/pycache`; the simulation Makefiles already do.

The per-area reports in `docs/results/` give the exact commands behind each
measurement. They are recorded as they were run on the development machine,
so they include its absolute paths, such as `/mnt/c/Users/<user>/...` for the
repository root under WSL.

## Documentation

- [docs/PLAN.md](docs/PLAN.md): targets, phases and environment
- [docs/Project_Proposal.pdf](docs/Project_Proposal.pdf): the original proposal
- Specifications: [quantisation](docs/quant_spec.md),
  [weights format](docs/weights_format.md),
  [QNC2 protocol](docs/qnc2_protocol.md),
  [front end](docs/frontend_spec.md), [engine](docs/engine.md),
  [streaming policy](docs/streaming.md)
- Software: [CPU runtime](docs/cpu_runtime.md),
  [engine driver](docs/engdrv.md), [captioning app](docs/app.md),
  [platform](docs/platform.md), [appliance](docs/product.md)
- Results: [summary](docs/results.md);
  [QuartzNet timing and PL/PS split](docs/results/quartznet.md)

## Third-party models and data

The QuartzNet 15x5 weights (NVIDIA NGC `stt_en_quartznet15x5`) and the
LibriSpeech corpus are downloaded by the scripts in `model/`. They are not
redistributed here and keep their own licences.
