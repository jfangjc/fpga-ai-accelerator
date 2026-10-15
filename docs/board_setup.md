# Board setup and recovery

## Check the board

```sh
uname -m
uname -r
python3 --version
command -v xmutil modinfo insmod rmmod
cat /sys/class/fpga_manager/fpga0/state
```

The supplied modules target `aarch64` and `6.8.0-1036-xilinx`. A KV260 image with a different kernel cannot use them. Rebuild the modules as described in [drivers.md](drivers.md), or use the recorded kernel environment. Do not force-load an incompatible module.

If `python3 -m venv` fails, install the venv package for that Python version through your board image's package manager. The pinned dependencies require Python 3.12 in the tested setup. The installer needs network access or a wheel cache with all packages in `requirements.txt`.

## Install and activate

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/setup_dependencies.py --archive /path/to/fpga-ai-accelerator-dependencies-1.0.0.zip
.venv/bin/python scripts/integrity.py
sudo .venv/bin/python scripts/setup_board.py --activate-bulk
```

Both `asr-v3-bulk` and `asr-v3-branch512-ab166` must have matching firmware files. If an existing directory has different bytes, preserve it under a different name before installing this project. Keep other FPGA applications and their files available if you intend to return to them.

The bootstrap command activates the bulk app as an explicit board-state change. It does not know how to restore an arbitrary app after a failed first-time activation. The inference runner has a narrower contract. It starts from a verified bulk state and restores that state after each run.

## Verify the starting state

```sh
sudo xmutil listapps
cat /sys/module/qn_tile/parameters/bulk_protocol
cat /sys/bus/platform/devices/asr-v3-bulk-clock/set_rate
test -c /dev/qn-tile
```

Expect the bulk app to own the active PL slot, the protocol value to be `1`, and a clock close to `100000000`. The tested board reports `99999999` because of integer clock-divider rounding. The scripts compare the loaded module's source version with the supplied module and require two PL DMA IRQs.

## Recover after an interrupted run

First check that no inference process still owns `/dev/qn-tile`. From the project directory:

```sh
sudo rmmod qn_tile
sudo xmutil unloadapp
sudo xmutil loadapp asr-v3-bulk
printf '100000000\n' | sudo tee /sys/bus/platform/devices/asr-v3-bulk-clock/set_rate
sudo insmod drivers/prebuilt/$(uname -r)/bulk/qn_tile.ko
```

An absent module or unloaded app may make the corresponding unload command fail. Inspect that state before proceeding. Use the saved `reports/local/before-*.json` file to restore the DMA IRQ masks if automatic restoration did not finish. The IRQ numbers may change after reloading an overlay, so match the two DMA IRQs rather than reusing unrelated interrupt numbers.

## Common failures

| Error | Action |
|---|---|
| Asset missing or changed | Run `scripts/setup_dependencies.py` with the pinned archive, then rerun the integrity check. |
| No prebuilt module for this kernel | Build both drivers for the running kernel. |
| Existing FPGA app differs | Preserve that app under another name, then install matching firmware. |
| Wrong protocol or module version | Activate the bulk app with the supplied bulk module before running inference. |
| Expected two PL DMA IRQs | Check overlay loading and the kernel log for AXI DMA probe errors. |
| FPGA transport failed | Read the kernel log and confirm image, driver, clock and input limits. |
| Output path already exists | Choose another output filename. |
