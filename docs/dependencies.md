# External dependencies

The repository excludes the model, copied ONNX Runtime headers, driver package, generated FPGA design files and all prebuilt native, kernel and FPGA binaries. Setup installs these files locally.

The Python dependencies in `requirements.txt` come from pip. Vivado, vendor IP libraries, the board operating system and system utilities require separate installation.

## Install the pinned assets

Use Python 3.12. The setup script uses only the standard library and does not need root access.

```sh
python3 scripts/setup_dependencies.py --archive /path/to/fpga-ai-accelerator-dependencies-1.0.0.zip
```

For a hosted archive:

```sh
python3 scripts/setup_dependencies.py --url https://YOUR_ASSET_HOST/fpga-ai-accelerator-dependencies-1.0.0.zip
```

Replace the example host with the maintainer's actual HTTPS URL. No public download location has been configured yet. You can also set `FPGA_AI_DEPENDENCIES_URL` or populate `download_url` in `config/dependencies.json`, then run the script without arguments.

The manifest pins the archive size, SHA256 and complete file list. Setup rejects unexpected paths, mismatched bytes and conflicting installed files before writing assets. Running it again with matching files is safe. Installed paths are ignored by Git.

Run `python3 scripts/integrity.py` afterward to check both original sources and installed dependencies. `--source-only` checks a fresh source checkout without external files.

## Model provenance

The archive contains the qualified INT8 ONNX graph, frontend constants and vocabulary derived from NVIDIA's `stt_en_citrinet_256_ls`, revision `c7cdacff0262bbb2a17771d20ec9f9f3b5cff6ed`. The upstream checkpoint is at [Hugging Face](https://huggingface.co/nvidia/stt_en_citrinet_256_ls/tree/c7cdacff0262bbb2a17771d20ec9f9f3b5cff6ed).

Downloading that raw checkpoint alone does not produce this runtime's optimized INT8 graph. Setup installs the prepared graph so the numerical and performance records still refer to the same model. This script does not claim to reproduce model export, calibration or training.

## Publish the archive

The prepared dependency archive is a separate deliverable beside the source folder. Upload that unchanged archive to an HTTPS file host accessible to your users. The project cannot supply a working public download URL until the maintainer hosts it.

Set `download_url` in `config/dependencies.json` to that URL if you want setup without arguments. Then run `python3 scripts/write_manifest.py` to update the source manifest and rerun the host checks. The archive SHA256 must stay unchanged when only its hosting location changes.

Do not add the installed assets to Git with a forced add. They belong to the external package. The source folder has no GitHub Actions configuration and does not require Git LFS.
