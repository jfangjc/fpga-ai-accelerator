# Setting up a KV260 with Ubuntu Server 24.04 LTS for stt-caption

This guide takes a KV260 Vision AI Starter Kit from a blank microSD card to
the `stt-caption` appliance running at boot. It was written for the board
used in this project:

| Item | Validated version |
|---|---|
| Ubuntu | Ubuntu Server 24.04.5 LTS for AMD Kria K26 (aarch64) |
| Kernel | `6.8.0-1036-xilinx` |
| Compiler | GCC 13.3.0 (native, on the board) |
| PL loader | `dfx-mgr` service and `xmutil` (preinstalled in the image) |
| Kernel arguments | `... uio_pdrv_genirq.of_id=generic-uio ... cma=800M` (image defaults) |

Other 24.04 kernels should work, but the installer then builds the DMA
buffer module for that kernel, and only the version above was measured.

## 1. What you need

- KV260 Vision AI Starter Kit with its 12 V power supply.
- A microSD card; 32 GB or larger is a comfortable size.
- Ethernet to the same network as your PC. The installer downloads one
  kernel module source from GitHub, so the board needs internet access.
- A DisplayPort monitor for the on-board captions. This is optional; the
  captions are also served over TCP.
- A PMOD I2S MEMS microphone breakout. A second microphone, a push button
  and an LED are optional.
- For the first boot, either a USB keyboard and the monitor, or the
  micro-USB cable for the serial console (115200 baud).

## 2. Install Ubuntu Server 24.04

1. Download the **Ubuntu Server 24.04 LTS** image for the AMD Kria K26 from
   <https://ubuntu.com/download/amd>. Use the Server image, not Desktop.
   On Desktop, the graphical session takes over the DisplayPort output that
   the captions use.
2. Write the `.img.xz` file to the card with balenaEtcher or Raspberry Pi
   Imager, or on Linux:
   ```sh
   xzcat ubuntu-24.04-*-kria-*.img.xz | sudo dd of=/dev/sdX bs=4M status=progress conv=fsync
   ```
3. Insert the card, connect Ethernet, and power the board on.
4. Log in as `ubuntu` with password `ubuntu`, then set a new password when
   prompted.
5. Update the system and reboot:
   ```sh
   sudo apt update && sudo apt full-upgrade -y
   sudo reboot
   ```

If the board does not boot the 24.04 image (for example, it stops before
Linux starts), its boot firmware is too old. Update the K26 boot firmware
following AMD's Kria instructions, then try again. On a running system,
`sudo xmutil bootfw_status` shows the installed version.

Find the board's address with `hostname -I`. Reserving it in your router's
DHCP settings keeps it the same across reboots.

## 3. Check the board

Run these on the board. The expected values are from the validated board.

```sh
lsb_release -d                 # Ubuntu 24.04.x LTS
uname -r                       # 6.8.0-...-xilinx
cat /proc/cmdline              # must contain uio_pdrv_genirq.of_id=generic-uio and cma=...
grep CmaTotal /proc/meminfo    # about 800 MB; the project needs at least 65 MiB
systemctl is-active dfx-mgr    # active
sudo xmutil listapps           # lists k26-starter-kits, active in slot 0 after boot
ls /sys/class/vtconsole/       # vtcon0, vtcon1 (vtcon1 is the frame buffer console)
```

- **Kernel arguments.** The overlay exposes the DMA engines, the front end
  and the accelerator as `generic-uio` devices, so the kernel needs
  `uio_pdrv_genirq.of_id=generic-uio`. The four DMA buffers (16 + 16 + 32 +
  1 MiB) come from CMA. Both settings are defaults of the Kria image, so
  normally nothing needs to change.
- **Other firmware apps.** Leave them in `/lib/firmware/xilinx/`; the
  appliance only replaces the boot default `k26-starter-kits` or its own
  `stt-*` overlays.

## 4. Install build packages

The installer compiles `sttcap` and the u-dma-buf kernel module on the board:

```sh
sudo apt install -y build-essential git linux-headers-$(uname -r)
gcc --version | head -1        # gcc 13.x
ls /lib/modules/$(uname -r)/build >/dev/null && echo headers OK
```

## 5. Wire the microphone

Power the board off before connecting anything to the PMOD connector (J2).
Use the 3.3 V and GND pins of the PMOD connector.

| Signal | PMOD pin | FPGA pin |
|---|---|---|
| Mic 1 SD (data) | 4 | E12 |
| Mic 1 WS | 2 | B10 |
| Mic 1 SCK | 6 | D11 |
| Mic 2 SD (optional) | 3 | E10 |
| Mic 2 WS (optional) | 1 | H12 |
| Mic 2 SCK (optional) | 5 | D10 |
| LED D1 (optional) | 7 | C11 |
| Button SW1 (optional) | 8 | B11 |

Tie microphone 1's L/R select pin **low** so that it transmits in the left
slot. The default setting `MIC=1` reads that slot. With two microphones,
set `MIC=beam` (see [product.md](product.md#settings-etcdefaultstt-caption)).

## 6. Prepare the PC

The tools in `tools/` run natively on Windows. They use PuTTY's `plink` and
`pscp`, which must both be on `PATH`, and Python 3 for the release build
(`winget install Python.Python.3.14`; open a new terminal afterwards). No WSL is
needed. Create a `.board.env` file in the repository root.
It is gitignored; never commit it.

```
BOARD_HOST=192.168.1.111
BOARD_USER=ubuntu
BOARD_PASS=<the password you set>
BOARD_HOSTKEY=SHA256:<fingerprint>
```

To get the fingerprint, run this on the board:

```sh
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

Check the connection with `./tools/board.ps1 run 'uname -r'`.

## 7. Install stt-caption

A release is a tarball `stt-caption-<date>-<git>.tar.gz`. It holds the
`stt-full150` overlay, the INT8 weights, the engine program, the source of
the board programs, the installer and the PC caption clients.
`tools/product_release.py` builds one and checks every asset against the
hashes validated on the board. The model assets are not stored in git; the
steps in [model/README.md](../model/README.md) write them to `data/export/`:
`quartznet_int8.bin` and `running_prior.json` (from `model/export.py`) and
the engine program `quartznet_qnc2.bin` (from `model/qnc2_export.py`).
Both flows are deterministic. On a fresh clone (2026-11-10), the model steps
and `./hw/build.ps1 full150` (86 minutes) reproduced all six validated hashes
byte for byte. An asset with a different hash is rejected: validate it on the
board first, then update the hashes in `tools/product_release.py`.

### Option A: from the Windows PC

`product_install.ps1` takes the overlay from `build/release/firmware/stt-full150/`.
If that folder is empty, it copies the overlay from `build/hw/full150/` (made by
`./hw/build.ps1 full150`). If there is no local build either, it downloads the
validated copy from the board's `~/stt/full150/firmware/`, which only a board
used for development has.

```powershell
tools/product_install.ps1            # build the release, install, enable at boot, start
tools/product_install.ps1 -NoEnable  # install and start, but do not start at boot
```

### Option B: by hand, from any PC

```sh
scp stt-caption-<tag>.tar.gz ubuntu@<board-ip>:~
ssh ubuntu@<board-ip>
tar -xzf stt-caption-<tag>.tar.gz
cd stt-caption-<tag>
sudo ./install.sh                 # or: sudo ./install.sh --no-enable --no-start
```

The installer:

1. verifies the release checksums;
2. builds `sttcap`, `fetool` and the engine driver with
   `-Wall -Wextra -Werror`, then runs the application's unit checks;
3. builds u-dma-buf (pinned commit `2cdfd21`) for the running kernel;
4. installs:
   - the programs and model to `/opt/stt-caption`;
   - the overlay to `/lib/firmware/xilinx/stt-full150/`;
   - the settings to `/etc/default/stt-caption` (an existing file is kept);
   - the systemd unit `stt-caption.service`;
5. enables and starts the service, unless you passed the flags above.

It ends with `PASS stt-caption <version> installed`.

## 8. Verify

**Microphone clock.** This loads the overlay and buffers, measures the I2S
clocks, then gives the console back:

```sh
sudo systemctl stop stt-caption
sudo /opt/stt-caption/bin/stt-caption prepare
sudo /opt/stt-caption/bin/fetool mic -t 2      # MIC CLOCK PASS (within 1%)
sudo /opt/stt-caption/bin/stt-caption cleanup
```

Speak during a second run and compare the `mic1 peak` value with the silent
one.

**Service.**

```sh
sudo systemctl start stt-caption
sudo /opt/stt-caption/bin/stt-caption status   # firmware: stt-full150, udmabuf0..3
journalctl -u stt-caption -b
```

**Captions on a PC.** Speak near the microphone and watch the monitor and a PC:

```sh
python3 tools/caption_live.py <board-ip>       # from the repository
python3 pc/caption_live.py <board-ip>          # from an unpacked release
```

**Boot.** If the service is enabled, reboot and check that captions start
without logging in:

```sh
sudo reboot
```

Each session's log is written to `/var/lib/stt-caption/sessions/latest/`.

## 9. Maintenance

- **Kernel updates.** `u-dma-buf.ko` is built for one kernel. After an
  update to a new kernel, reboot and run the installer again. Until then the
  service fails when `stt-caption prepare` loads the module. To control when this happens, hold
  the kernel packages: list them with `dpkg -l 'linux-*xilinx*'`, then run
  `sudo apt-mark hold <package>` for each.
- **Settings.** Edit `/etc/default/stt-caption`, then run
  `sudo systemctl restart stt-caption`. The keys are described in
  [product.md](product.md#settings-etcdefaultstt-caption).
- **Using the PL for something else.** Run `sudo systemctl stop stt-caption`
  first. If another project's overlay is loaded at boot, the service exits
  with code 75 and does not restart, rather than replace it.
- **Buffers.** If `missing udmabuf` appears with the right kernel, CMA was
  fragmented when the module loaded; reboot.
- **Uninstall.** From the release directory, run `sudo ./uninstall.sh`.
  Add `--purge` to also remove the logs, the settings and the overlay.

For other problems, see the troubleshooting table in [product.md](product.md#troubleshooting).
