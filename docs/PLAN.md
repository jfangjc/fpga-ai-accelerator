# STT accelerator — engineering plan

Source of truth for scope: `docs/Project_Proposal.pdf`. Pin map: `rtl/kria-constraints.xdc`.
Goal: live QuartzNet 15x5 captioning on the KV260 with convolutions on a PL INT8 engine,
plus every stretch/reach goal. Testing injects audio (WAV replay) instead of a live mic,
but the I2S path is still built.

## Targets (Table VII)
| Metric | Core | Stretch |
|---|---|---|
| Speed-up vs CPU INT8 (same model) | >= 2x | >= 3x |
| Preview latency p95 | <= 1000 ms | <= 250 ms |
| WER (finals) | <= 10 % | INT8 <= float + 1 pt |
| Board power | <= 20 W | <= 15 W |
| Reach | Moonshine Streaming Tiny encoder on the engine (now a separate `moonshine` repository) | |
| Stretch features | beamformer (2 mics), button, LED, SD logging, TCP captions | |

### Measured CPU baseline → engine targets (2026-11-02)
CPU INT8 runtime (NEON, 4 threads, idle board): 3 s window (300 frames, 2.865 GMAC)
= 217 ms median, 13.1 GMAC/s, 64 % of the measured 20.5 GMAC/s NEON peak.
Engine end-to-end per 3 s window (incl. DMA, cache sync, driver) must therefore be
**≤ 109 ms for 2× (core)** and **≤ 72 ms for 3× (stretch)**. 512 MACs at 100 MHz
(51.2 GMAC/s peak, ~50 % modelled utilisation ≈ 110 ms) is NOT enough: S2 must raise
utilisation (overlap depthwise with pointwise, hide weight streaming) and/or clock
(150–200 MHz engine domain) and/or MACs per DSP (INT8 operand packing).

## Environments
- Windows host: repo at `C:\Users\jfang\Documents\stt-accelerator`.
- WSL `FedoraLinux-44` (repo at `/mnt/c/Users/jfang/Documents/stt-accelerator`): verilator,
  iverilog, yosys, gcc, python3. Use it for RTL sim and Linux tooling.
- Vivado 2026.1 (Windows): `C:\AMDDesignTools\2026.1\Vivado\bin\vivado.bat` (batch mode:
  `vivado.bat -mode batch -source x.tcl`). Part `xck26-sfvc784-2LV-c`, board part
  `xilinx.com:kv260_som:part0:1.4` (also 2.0). Vitis 2026.1 alongside (`Vitis\bin\bootgen.bat`,
  `xsct.bat`). Host: 20 threads, 32 GB RAM — run at most one Vivado implementation at a time.
- Board: `ubuntu@192.168.1.111` (credentials in gitignored `.board.env`; never commit them), Ubuntu 24.04 aarch64, 4x A53,
  4 GB RAM, sudo works with the login password, `/dev/udmabuf`, `/dev/uio*`, `cma=800M`,
  apps loaded with `xmutil` from `/lib/firmware/xilinx/<app>/`.
  Key auth is NOT set up; use the pinned host key with plink/pscp:
  `plink -batch -ssh -hostkey "SHA256:LKipAYiol8maTkeoe4VDOwZRuJLRLhyp1Nd7iSMk+1I" -pw $BOARD_PASS ubuntu@192.168.1.111 "<cmd>"`
  `pscp -batch -hostkey "SHA256:LKipAYiol8maTkeoe4VDOwZRuJLRLhyp1Nd7iSMk+1I" -pw $BOARD_PASS <src> ubuntu@192.168.1.111:<dst>`
  Do not install SSH keys or change board auth. Put project files under `~/stt/` on the board.
  Pre-existing `asr-v3-*` / `voice-*` firmware apps on the board are not ours; leave them alone.

## Repo layout
```
model/      Python: QuartzNet weights export, BN folding, float + INT8 golden models,
            quantisation, log-mel front end reference, CTC, WER eval
sw/         C/C++ for the board: CPU INT8 runtime (NEON, threads), PL driver (uio/udmabuf),
            streaming pipeline, VAD, caption states, display/TCP/SD, test harness
rtl/        SystemVerilog: INT8 engine (systolic array, depthwise, requant, QNC2 parser),
            I2S rx, FFT + mel front end, beamformer, AXI wrappers
sim/        Verilator/iverilog testbenches driven by golden vectors from model/
hw/         Vivado Tcl: block design, constraints, build scripts, dtbo, shell.json
tools/      bench scripts run from the PC: replay, WER/latency scoring, power
docs/       specs (quant_spec.md, qnc2_protocol.md, results.md)
data/       (gitignored) datasets, exported weights, golden vectors
```

## Phases
1. **Model + golden** (`model/`): QuartzNet 15x5 float reference, INT8 quantisation spec
   (INT8 weights per-out-channel, UINT8 activations, int32 accum, bias + Q31 requant,
   BN folded), bit-exact integer golden model, WER on 40 LibriSpeech test-clean clips.
2. **CPU INT8 baseline** (`sw/`): NEON 4-thread runtime, bit-exact with golden. This is
   the fallback demo and the A/B denominator.
3. **RTL + sim**: engine S1 (pointwise) then S2 (pointwise + depthwise, one command per
   chunk); front end (I2S, FFT, mel, sample-injection path for replay); beamformer.
   Bit-exact against golden vectors in Verilator.
4. **Vivado build** (needs a Vivado host): block design, 100 MHz timing, bitstream, dtbo.
5. **Integration on board**: driver, streaming pipeline, captions, A/B, latency, WER, power,
   30 min soak, stretch I/O.
6. **Reach**: Moonshine Streaming Tiny encoder matmuls on the same engine
   (moved to the separate `moonshine` repository).

## Conventions
- Every hardware/software block is checked bit-exact against `model/` golden integer model.
- Results and measurements go in `docs/results/<area>.md` (one file per subsystem, owned by
  that subsystem) with the command that produced them. `docs/results.md` is the senior
  engineer's consolidated summary; workers do not edit it.
- Hardware acceptance: every RTL block is lint clean, bit-exact in Verilator, and closes
  100 MHz post-route in Vivado OOC within its resource budget before it is integrated.
- Commit after each working step with a clear message.
