# stt-caption: live captioning appliance (KV260)

The KV260 boots straight into live captioning from a PMOD I2S MEMS microphone.
Captions appear on the DisplayPort monitor and are streamed over TCP to any PC
on the network. Speech features (pre-emphasis, FFT, log-mel) are computed in
the PL front end. The QuartzNet 15x5 INT8 encoder runs on the PL engine at 150 MHz
(`stt-full150` overlay); the A53 cores run VAD, CTC decoding and the display.

## Hardware

| Item | Connection |
|---|---|
| Microphone 1 (default) | PMOD pin 4 SD (`E12`), pin 2 WS (`B10`), pin 6 SCK (`D11`), 3V3, GND; L/R select **low** (left slot) |
| Microphone 2 (optional) | PMOD pin 3 SD (`E10`), pin 1 WS (`H12`), pin 5 SCK (`D10`); same clocks as mic 1 |
| Monitor | DisplayPort (640x480 caption framebuffer) |
| Button / LED | PMOD button SW1: end the session and start a fresh one. LED D1: lit while speech is detected |
| Network | Ethernet; TCP port 8765 serves caption events |

The receiver clocks the microphone at 1.024 MHz BCLK / 16 kHz WS (measured
99.998 frames/s, -0.002 %) and keeps the top 18 of 24 data bits.

## Install or update

To prepare a new board (Ubuntu Server 24.04, packages, wiring, `.board.env`),
see [setup_kv260_ubuntu24.md](setup_kv260_ubuntu24.md).

From the development PC (repository root, board reachable via `.board.env`):

```powershell
tools/product_install.ps1            # build release, install, enable at boot, start
tools/product_install.ps1 -NoEnable  # install without starting at boot
```

This runs `tools/product_release.sh`, which builds
`build/release/stt-caption-<date>-<git>.tar.gz` after checking every model, program and
firmware hash against the versions validated on the board. It then uploads the
tarball and runs `install.sh` as root. The installer does the following:

- builds `sttcap` natively with `-Wall -Wextra -Werror` and runs the unit checks;
- builds the pinned u-dma-buf module;
- installs the files listed below;
- enables `stt-caption.service`.

| Installed file | Purpose |
|---|---|
| `/opt/stt-caption/bin/{sttcap,fetool,stt-caption}` | application, front-end tool, control script |
| `/opt/stt-caption/share/{quartznet_int8.bin,running_prior.json,program.qnp2}` | INT8 weights, dev-clean prior, engine program |
| `/opt/stt-caption/lib/u-dma-buf.ko` | DMA buffer module for the running kernel |
| `/lib/firmware/xilinx/stt-full150/` | PL overlay |
| `/etc/default/stt-caption` | settings (kept across updates) |

To uninstall, run `sudo ./uninstall.sh` from the release directory. Adding
`--purge` also removes the logs, the settings and the overlay.

## Operation

At boot the service runs `stt-caption prepare`. If the boot-default
`k26-starter-kits` (or another `stt-*` overlay) is active, it loads `stt-full150`.
It refuses to replace any other project's overlay (exit 75, no restart). It
then loads the four DMA buffers and detaches the text console from the
framebuffer. `stt-caption run` starts `sttcap` on the live microphone.

- **Listening:** the screen is blank and the LED is off.
- **Speaking:** the LED is on. The preview updates every 120 ms and shows the
  stable text followed by an amber tentative suffix.
- **Finalising and Committed:** about 0.7 s after speech ends, the phrase is
  re-decoded as a whole and replaced in place by the final text, shown in
  white. On the test set this takes about 1.4 s from the end of a clip to the
  caption on a PC.
- **Button:** pressing it ends the session cleanly. When the service runs it,
  systemd starts a new session within about 1 s, so the screen clears.

Each session writes a directory under `/var/lib/stt-caption/sessions/` (the
newest 20 are kept; `latest` links to the current one). It holds:

- `session.jsonl`: every caption event;
- `stderr.txt`: diagnostics and capture statistics;
- `caption.ppm`: a snapshot of the screen, written every 5 s;
- `command.txt`: the exact command used.

```sh
sudo systemctl status stt-caption       # service state
sudo /opt/stt-caption/bin/stt-caption status
sudo systemctl stop stt-caption         # release the PL (dev work)
sudo systemctl restart stt-caption      # after editing /etc/default/stt-caption
journalctl -u stt-caption -b
```

### Captions on a PC

For a demo, use the live view. Each finished phrase is printed once on its
own line. The phrase being spoken is redrawn in place below them: settled
words in white, words that may still change in amber, and grey while the
final is being computed. The final text then replaces it.

```sh
python3 tools/caption_live.py <board-ip>                      # from the repository
python3 pc/caption_live.py <board-ip>                         # from the release directory
python3 tools/caption_live.py <board-ip> --times --output transcript.txt
python3 tools/caption_live.py --replay session.jsonl --speed 2   # replay a saved session log
```

It waits for the board if the board is not up yet. When the button or a
restart begins a new session, it reconnects and prints a `--- new session ---`
line. A client that joins mid-session first shows the earlier text of that
session as one block. Press Ctrl-C to stop. Piped to a file or another
program, the live view prints only the final phrases.

`caption_client.py` prints every event, which is useful for debugging:

```sh
python3 tools/caption_client.py <board-ip> --timestamps      # from the repository
python3 pc/caption_client.py <board-ip> --timestamps         # from the release directory
```

Both clients use only the Python standard library. On this Windows PC, run
them through WSL (`wsl python3 tools/caption_live.py 192.168.1.111`).

A client can connect at any time. It first receives a snapshot of the current
caption state, then each event. Every event carries the full caption state
(`committed_prefix`, `segments`, `caption`), so clients can redraw from any
single event.

## Settings (`/etc/default/stt-caption`)

| Key | Default | Meaning |
|---|---|---|
| `MIC` | `1` | `1`, `2`, or `beam` (two-microphone delay-and-sum beamformer) |
| `MIC_GAIN_DB` | `20` | software gain on microphone band power before the log (an exact digital gain ahead of the FFT) |
| `VAD_SNR_DB` | `8` | speech threshold above the tracked noise floor |
| `PORT` | `8765` | TCP caption port |
| `BACKEND` | `pl` | `pl` = FPGA engine, `cpu` = A53 INT8 fallback |
| `FINAL_BACKEND` | `auto` | finals up to 6.4 s on the PL, longer ones on a CPU worker |
| `EXTRA_ARGS` | empty | more `sttcap` options, e.g. `--vad-log vad.csv` |

### How the microphone VAD works

The VAD runs on every 10 ms PL frame. It sums the linear mel energy of
bands 1–63. Band 0 is excluded because the MEMS microphone's DC offset
(about -33 dBFS) leaks into it. The VAD tracks the noise floor as the minimum
of 50-frame block minima over the previous 5 s. A frame counts as speech when
its energy exceeds the floor by `VAD_SNR_DB`. The first 0.3 s after start is
ignored while the microphone settles. Phrases use the replay policy: 0.2 s
lead-in, end after 0.7 s of silence, and a 15 s cap.

## Verification

See [results/mic.md](results/mic.md) for the acoustic tests: tone
identification, the live end-to-end WER through the speaker and microphone,
and the service tests.

## Troubleshooting

| Symptom | Check |
|---|---|
| Service exits with 75 | another project's overlay is loaded; unload it or reboot |
| No captions, LED never lights | `sudo systemctl stop stt-caption; sudo /opt/stt-caption/bin/fetool mic -t 2` should report `MIC CLOCK PASS`. Then speak and compare `mic1 peak` with the silent value |
| Words missing at phrase starts | lower `VAD_SNR_DB` (6) or move the microphone closer |
| `missing udmabuf` | reboot (the module needs all four buffers allocated together) |
| Kernel updated | re-run the installer to rebuild u-dma-buf |
