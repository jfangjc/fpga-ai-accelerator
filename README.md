# Live FPGA-Accelerated Speech-to-Text on the Kria KV260

## Setup
If you use `nix`, you can set up `cmake`, `make`, `clang` and `python3` using just:

```bash
nix develop
```

Source Vivado by using its included `settings64.sh` or `settings64.bat` for Windows (set `<version>` to your Vivado version, like `2026.1`):

```bash
source /tools/Xilinx/<version>/Vivado/settings64.sh
```

Or for Windows:

```powershell
C:\AMDDesignTools\<version>\Vivado\settings64.bat
```

Next is to download and setup the model. First run the below from the root of the project:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r scripts/requirements.txt
```

Or for Windows:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r scripts/requirements.txt
```

Next run the setup script to download and convert the model from FP32 to INT8:

```bash
python3 scripts/setup.py
```

All downloaded data and the converted model live under `data`. You can run the converted model on a 16 kHz mono WAV file using the provided `run.py`:

```bash
python3 scripts/run.py speech.wav
```

Now build the bitstream. Use the provided `build.tcl` in `./hw` and run the below from the root of the project:

```bash
vivado -mode batch -source hw/build.tcl
```

Add `-tclargs nomic` to build without the microphone, LED and button. The bitstream, device tree overlay and `shell.json` are written to `build/hw/kv260/stt-kv260`.

You can run all the simulation using `run.tcl` in `./sim` at the root of the project:

```bash
vivado -mode batch -source sim/run.tcl
```

Then build the board programs `caption`, `screen` and `transcript`:

```bash
cmake -S app -B build/app -G "Unix Makefiles"
cmake --build build/app
```

On Windows this cross-compiles with the Vitis aarch64 toolchain. On Linux it uses the host compiler, so run it on the board itself.

## Running the project
Copy the built files to the board:

- `build/hw/kv260/stt-kv260` to `/lib/firmware/xilinx/stt-kv260`
- `build/app/caption`, `build/app/screen` and `build/app/transcript`
- `data/export/quartznet_engine.bin`, `data/export/quartznet_int8.bin` and `data/export/running_prior.json`

Load the overlay on the board:

```bash
sudo xmutil unloadapp
sudo xmutil loadapp stt-kv260
```

The programs also need the `u-dma-buf` kernel module loaded with four buffers, `udmabuf0` to `udmabuf3`.

For live captions from the microphone, run either the `hybrid` (FPGA) or `cpu` runner. `hybrid` takes the engine program `quartznet_engine.bin`, and `cpu` takes the INT8 weights `quartznet_int8.bin`:

```bash
sudo ./caption hybrid quartznet_engine.bin running_prior.json
sudo ./caption cpu quartznet_int8.bin running_prior.json
```

To turn a 16 kHz mono 16-bit WAV file into text:

```bash
sudo ./transcript hybrid quartznet_engine.bin running_prior.json speech.wav transcript.txt
```

To show the live captions on a monitor plugged into the DisplayPort, run `screen` instead of `caption`, with the same arguments:

```bash
sudo ./screen hybrid quartznet_engine.bin running_prior.json
```

A status strip along the bottom shows whether someone is speaking, the microphone level, the runner, the last decode time and the caption delay. After 5 s of silence the screen clears. Add `--no-status` to hide the strip, or `--clear-after 0` to keep the captions on screen. The default colours are the pastel yellow `butter` theme and add `--theme night` for a dark screen. Add `--settle 3` to only settle a word once 3 previews in a row agree on it.

To test the display layouts and the settle rule, copy `build/app/test_display` and `build/app/test_settle` to the board and run:

```bash
./test_display
./test_settle
```
