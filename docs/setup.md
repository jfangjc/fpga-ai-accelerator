# Setup

The captioner runs on an AMD Kria KV260. A Windows PC prepares the model and the FPGA design and installs them
on the board.

## What you need

- A KV260 running Kria Ubuntu 24.04, on the same network as the PC.
- A PMOD I2S MEMS microphone on PMOD J2: pin 2 word select, pin 4 data, pin 6 bit clock, with its L/R select tied
  low (the left channel). Pin 7 drives an LED, pin 8 reads a button.
- A DisplayPort monitor, optional, for on-screen captions.
- On the PC: Python 3.14 and Vivado 2026.1, with `vivado` on the PATH
  (`C:\AMDDesignTools\2026.1\Vivado\settings64.bat`).

## 1. Model

```
python -m venv .venv
.venv\Scripts\activate
pip install -r scripts/requirements.txt
python scripts/setup.py
```

This downloads QuartzNet 15x5 and the LibriSpeech dev-clean clips (about 0.4 GB) and converts the model to INT8,
which takes about 20 minutes. It writes `data/export/`: `quartznet_engine.bin`, the program the FPGA engine runs,
and `running_prior.json`, the board's starting normalisation statistics and input scale.

## 2. FPGA design

```
vivado -mode batch -source hw/build.tcl                 # add -tclargs nomic to leave out the microphone
```

Vivado builds the block design and the bitstream (about 45 minutes) and packages the overlay in
`build/hw/kv260/stt-kv260`. A `nomic` build has no microphone, LED or button pins, for evaluation by injection
only; the install takes whichever was built last.

## 3. Install on the board

Put the board's address and login in `.board.env` at the repository root (it is ignored by git):

```
BOARD_HOST=192.168.1.111
BOARD_USER=ubuntu
BOARD_PASS=...
BOARD_HOSTKEY=SHA256:...
```

`BOARD_HOSTKEY` is the board's SSH host key fingerprint (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the
board). Then:

```
scripts/install.ps1            # add -Enable to start captioning at every boot
```

This uploads the sources, the model files and the overlay, builds `sttcap` and the u-dma-buf driver on the board,
and installs them in `/opt/stt-caption` with the `stt-caption` service.

To remove it: `sudo /opt/stt-caption/uninstall.sh` (add `--purge` to also remove the settings, the session logs
and the overlay).

## RTL tests

```
vivado -mode batch -source sim/run.tcl
```

This runs the two front end and two engine testbenches in `sim/`; each prints `PASS` or a `FAIL` message.
