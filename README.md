# FPGA AI accelerator

This project runs a Citrinet INT8 speech model on a KV260. The FPGA executes retained depthwise and pointwise convolution blocks. ONNX Runtime executes the remaining graph on the ARM CPU.

The supported input is an uncompressed 16 kHz mono WAV with 320 to 40,959 samples. The checkpoint uses the whole input context. Live microphone transcription is not qualified.

## Repository contents

The source folder contains original RTL, runtime and graph code, tests, build scripts and documentation. It has no GitHub Actions workflow. Model assets, ONNX Runtime headers, drivers, generated FPGA design files and prebuilt binaries install separately through the dependency setup script.

## Get the dependencies

The maintainer must make the pinned dependency archive available separately. The archive contains the exact model and firmware used for the recorded tests. See [dependency setup and publication](docs/dependencies.md). Git LFS is not required for this source folder.

On the KV260, from the project directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/setup_dependencies.py --archive /path/to/fpga-ai-accelerator-dependencies-1.0.0.zip
.venv/bin/python scripts/integrity.py
sudo .venv/bin/python scripts/setup_board.py --activate-bulk
```

To download the archive, replace `--archive PATH` with `--url HTTPS_URL`. The script checks the archive and each file against pinned SHA256 hashes before installation. It refuses to replace changed files. Downloaded files are ignored by Git. Do not run dependency setup with sudo.

Python packages install through pip. `setup_dependencies.py` installs the model, headers, firmware and driver package.

## Tested configuration

| Component | Version or setting |
|---|---|
| Board | AMD Kria KV260, K26 device |
| Linux kernel | `6.8.0-1036-xilinx`, ARM64 Ubuntu |
| Python | 3.12.3 |
| Inference FPGA clock | 166.667 MHz |
| Accelerator | 512 pointwise MACs and 64 depthwise lanes |
| FPGA tools | Vivado 2026.1, build 6511674 |

The prebuilt modules require the exact kernel above. Other kernels require matching headers, rebuilt drivers and a new physical test. See [drivers](docs/drivers.md) and [board setup](docs/board_setup.md).

The setup command installs both FPGA apps under `/lib/firmware/xilinx`. It refuses to overwrite different installed apps with the same names. `--activate-bulk` replaces the active PL app with the supplied 100 MHz bulk image, the starting state and rollback target for inference.

## Transcribe a WAV

```sh
sudo .venv/bin/python scripts/run_board.py -- /path/to/clip.wav \
  --runs 3 --verify-cpu --out reports/local/result.json
```

Supply your own PCM16, PCM24 or PCM32 recording. The command rejects other sample rates, stereo audio and unsupported lengths. It does not resample or truncate recordings.

The runner activates inference firmware and restores the bulk image, driver, 100 MHz clock and DMA IRQ masks on exit. It attempts restoration after errors and interrupts. Power loss and SIGKILL require manual recovery.

The result separates initialization, the first physical call and warm calls. The first call registers 24 programs and executes 24 transactions. Each warm call reuses those registrations. `--verify-cpu` compares every warm output tensor, decoded text and output length with the INT8 CPU graph. A failed comparison aborts the command. Hardware errors do not trigger CPU fallback.

## Results and limits

The earlier clean MVP test checked 300 paired warm runs for each duration. All 900 full-logit comparisons passed. The CPU column uses the fastest recorded comparator on the same recording.

| Audio | CPU p50 | FPGA p50 | Speedup |
|---|---:|---:|---:|
| 0.5 s | 52.130 ms | 26.114 ms | 1.996x |
| 1 s | 93.814 ms | 35.750 ms | 2.624x |
| 2.5 s | 145.449 ms | 59.443 ms | 2.447x |

The public entry point later passed three warm full-logit comparisons and rollback on the board. That smoke test measured 63.191 seconds of initialization and a 36.217 ms warm median on a one-second recording.

The shortest benchmark input narrowly missed a strict 2x speedup. The measurements use prefixes of one recording. Representative word error rate, live capture, longer audio and streaming transcript stability remain unqualified. Moving the dependencies out of the repository does not broaden that qualification. See [architecture](docs/architecture.md) for the execution path. Detailed evidence and validation history stay local and are excluded from Git.

## Build and test

Host checks work without downloading the model or FPGA assets:

```sh
.venv/bin/python scripts/integrity.py --source-only
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/check_distribution.py
```

[Build instructions](docs/build.md) cover Vivado project recreation, RTL simulations and native libraries. Install external dependencies first for FPGA and native builds. Rebuilt binaries need new integrity records and physical qualification.

## Publishing

No Git repository has been initialized. Before publishing this source folder, host the dependency archive and set `download_url` in `config/dependencies.json`, or provide the URL to users separately. Run `python scripts/write_manifest.py` after changing that configuration. The archive itself, downloaded files, recordings, evidence and reports stay outside the commit. Use `python scripts/check_distribution.py --source-only` on the source distribution to check the commit file list and confirm that external files are absent.
