# Test harnesses

Integration and board tests, one folder per test. Unit tests stay with their
code (`make check` in `sw/*` and `sim/*`). Each report in `docs/results/`
gives the exact commands; evidence goes to the matching folder in
`results/`.

| Folder | Test | Report |
|---|---|---|
| `i1/` | I1: WAV replay through the PL front end to DDR, bit-exact against the Python reference | [i1](../docs/results/i1.md) |
| `i2/` | I2: saved feature windows through the engine, CTC identical to the CPU reference | [i2](../docs/results/i2.md) |
| `full150/` | acceptance of the 150 MHz `stt-full150` overlay (paired CPU/PL timing, I2) | [full150](../docs/results/full150.md) |
| `t1/` | T1: paced 40-clip captioning, latency, WER, CPU/PL A/B and power | [t1](../docs/results/t1.md) |
| `t2/` | T2: deployment, 30-minute soak, power and extras | [t2](../docs/results/t2.md) |
| `demo/` | demo rehearsal: framebuffer, TCP client, SD log and caption identity | [t2](../docs/results/t2.md) |
| `mic/` | live microphone: speaker-to-microphone acceptance runs | [mic](../docs/results/mic.md) |
| `cpu/` | CPU INT8 runtime correctness, speed and WER on the board | [cpu](../docs/results/cpu.md) |
| `app/` | captioning app checks: I3, smoke, stall, late join, soak | [app](../docs/results/app.md) |
| `platform/` | platform IRQ soak | [platform](../docs/platform.md) |

Board-side scripts run from a copy of this repository on the board, such as
`~/stt/t2/src` unpacked from `git archive HEAD`. They therefore use the same
relative paths as here (`build/sw/app/sttcap`, `build/tests/full150/full150_probe`).
PC-side PowerShell scripts use `tools/board.ps1`, which reads the ignored
`.board.env`.
