# Results

## Model

Float and INT8 acceptance measurements are complete.
The proposal's 5.43% is a historical baseline on an unidentified 40-clip list,
not a measurement of this implementation or its new deterministic subset.

Checkpoint SHA256:
`1b9b7b87a9277e6fef164d8f99d1226f0511af154423bbf919b920421ac9602f`.
Measured with `wsl -d FedoraLinux-44 -- /usr/bin/python3 model/download.py`.
Source is NVIDIA NGC `stt_en_quartznet15x5`, version `1.0.0rc1`.

Reproduction commands (repository root; WSL FedoraLinux-44):

```
wsl -d FedoraLinux-44 -- .venv/bin/python model/download.py --datasets
wsl -d FedoraLinux-44 -- .venv/bin/python model/verify.py
wsl -d FedoraLinux-44 -- .venv/bin/python model/eval_wer.py --select --out data/wer_float.json
wsl -d FedoraLinux-44 -- .venv/bin/python model/calibrate.py --count 100 --percentile 99.99
wsl -d FedoraLinux-44 -- .venv/bin/python model/eval_wer.py --int8 data/int8.pkl --out data/wer_int8.json
wsl -d FedoraLinux-44 -- .venv/bin/python model/eval_wer.py --int8 data/int8.pkl --mode running --out data/wer_running.json
wsl -d FedoraLinux-44 -- .venv/bin/python model/export.py --vectors
wsl -d FedoraLinux-44 -- .venv/bin/python model/stream_sim.py
```

| Measurement | Result |
|---|---|
|Float WER, selected test-clean 40|4.363207547% (37 / 848 words), 305.85 s|
|INT8 WER, utterance normalization|4.599056604% (39 / 848 words); +0.235849057 percentage points vs float|
|INT8 WER, running normalization|4.834905660% (41 / 848 words); +0.471698113 percentage points vs float|
|INT8 1 s window stitched preview / VAD final WER, first 3 clips|90.76923077% / 4.615384615% (65 words)|
|INT8 3 s window, 400 ms withheld context, preview / final WER, same 3 clips|32.30769231% / 4.615384615% (65 words)|
|BN folded/unfolded max absolute error, seeded [1,64,112], valid T=101|0.000017583370208740234|
|Checkpoint mel bank/Hann comparison|Pass|
|Running normalization partition invariance|Pass, bit-exact|
|Q31 half-away and convolution boundary checks|Pass|
|Architectural receptive field|8057 frames = 80.57 s|
|Symmetric right context|40.28 s|
|MAC per audio second (50 output frames)|942,352,000|
|MAC per 1 s preview window|942,352,000|
|MAC per actual padded 112-frame preview allocation|1,055,434,240|

The completed numerical checks used native WSL temporary dependencies because
package extraction into the Windows-mounted `.venv` was slow:

```
wsl -d FedoraLinux-44 -- env PYTHONPATH=/tmp/qn_deps /usr/bin/python3 model/verify.py
wsl -d FedoraLinux-44 -- env PYTHONPATH=/tmp/qn_deps /usr/bin/python3 model/stream_sim.py
wsl -d FedoraLinux-44 -- env PYTHONPATH=/tmp/qn_deps /usr/bin/python3 model/eval_wer.py --select --out data/wer_float.json
wsl -d FedoraLinux-44 -- env PYTHONPATH=/tmp/qn_deps /usr/bin/python3 model/calibrate.py --count 100 --percentile 99.99
wsl -d FedoraLinux-44 -- env PYTHONPATH=/tmp/qn_deps /usr/bin/python3 model/eval_wer.py --int8 data/int8.pkl --out data/wer_int8.json
wsl -d FedoraLinux-44 -- env PYTHONPATH=/tmp/qn_deps /usr/bin/python3 model/eval_wer.py --int8 data/int8.pkl --mode running --out data/wer_running.json
wsl -d FedoraLinux-44 -- env PYTHONPATH=/tmp/qn_deps /usr/bin/python3 model/export.py --vectors
wsl -d FedoraLinux-44 -- env PYTHONPATH=/tmp/qn_deps /usr/bin/python3 model/verify_export.py
wsl -d FedoraLinux-44 -- env PYTHONPATH=/tmp/qn_deps /usr/bin/python3 model/stream_sim.py --eval --clips 3 --out data/stream_3.json
wsl -d FedoraLinux-44 -- env PYTHONPATH=/tmp/qn_deps /usr/bin/python3 model/stream_sim.py --eval --clips 3 --window 3 --lookahead 0.4 --out data/stream_3_context.json
```

The latter MAC count is asymptotic and excludes frontend, normalization, bias,
activation and residual-add arithmetic. Actual window MACs use rounded/padded
frame counts and can be larger. A 120 ms update cadence with a 1 s window is
approximately 7.853 GMAC/s of convolution work. Full-context-equivalent 1 s
previews are impossible at sub-second latency with this unmodified symmetric
network: future context alone is 40.28 s. Use approximate previews and full
phrase finals, and measure their WER separately.
The 112-frame allocated count is from
`wsl -d FedoraLinux-44 -- env PYTHONPATH=/tmp/qn_deps /usr/bin/python3 model/stream_sim.py --out data/architecture.json`;
it includes convolution work on normalized zero padding to 16 frames.
The installed `.venv` also passed
`wsl -d FedoraLinux-44 -- .venv/bin/python model/verify.py` with the same
1.7583370208740234e-5 folding error. Test environment: WSL Python 3.14,
Torch 2.15.0+cpu, NumPy 2.5.4, librosa 1.0.0, SoundFile 0.14.0, PyYAML 6.0.3.

Selection rule: increasing absolute difference of clip duration from 7.675 s,
utterance id tie-break, one utterance per speaker until 40; saved sorted by id.
Calibration: first 100 lexicographically sorted dev-clean utterances only.

The blob is 20,049,332 bytes, with 186 descriptors and 558 independently checked
layer vectors over three clips. Dimensions measured with
`wsl -d FedoraLinux-44 -- env PYTHONPATH=/tmp/qn_deps /usr/bin/python3 model/verify_export.py`.
Blob tiling/parameters and all vectors pass `model/verify_export.py` above.
Both INT8 normalization modes pass the float + 1 point stretch target on full
clips. Running uses a dev-only, 100-frame-equivalent Welford prior; its extra
two errors over utterance-normalized INT8 support this estimator for further
streaming tests, not a general claim of streaming equivalence.
Calibration caught and fixed constant-BN-channel bias overflow and tiny Q31
scales (large right shifts); neither is silently wrapped.

Streaming simulation implements energy VAD (RMS threshold 0.01,
700 ms silence, 200 ms lead-in), end-of-clip phrase flush and independent
120 ms previews. Preview scoring stitches newly emitted logits, carrying
normalization state across each phrase. Default one-second, zero-lookahead
previews are unusable on this study; finals remain accurate. This is a three-clip
diagnostic, not a general streaming accuracy claim.
The 3 s / 400 ms study improves preview WER but still does not establish usable
preview quality. Recommend that setting for continued experiments: 120 ms hop,
2.88 s overlap, 400 ms withheld context plus 16 ms frontend centering delay,
and 700 ms VAD silence before utterance-normalized final decoding. Theoretical
validity would require 40.28 s of both left and right context; this approximate
setting intentionally cannot meet that requirement. Its convolution cost is
2,827,056,000 MAC/update before frame padding (23.5588 GMAC/s at 120 ms cadence).
Three seconds is a 1 s preview region plus 2 s historical window budget;
the held-back 400 ms reduces the region that can be emitted immediately.
The overlap and held-back context are empirical, not a valid-receptive-field
proof. Frozen frame stitching causes repeated characters; preview revision or
token consensus remains a product integration issue. Full-clip INT8 acceptance
has passed; live-preview quality has not.

## Front end

Implemented 100 MHz AXI-Lite/AXI-Stream front end, shared 1024-sample FIFO,
fractional-clock dual I2S, iterative radix-2 FFT, 64 Slaney mel bands, packet
sequence/exponent, coalesced level IRQ, DC-filtered integer delay-and-sum,
LED and debounced button. Register and integration contract: frontend_spec.md.

Verification (2026-11-01, FedoraLinux-44, Verilator 5.046):

- `make -C sim/frontend test`: 40 frames / 2560 uint64 bands bit-exact,
  randomized input gaps/output backpressure; 810602 cycles total.
- `make -C sim/frontend test-top`: 40 frames / 2560 bands bit-exact through
  AXI injection, elastic FIFO, DSP and output packetizer; frame IRQ observed.
  AW and W are deliberately separated by three clocks.
- `make -C sim/frontend test-i2s`: both microphones and both channel selections
  match behavioral transmitter; 1024 rising BCLK edges in 100000 clocks (1 ms),
  frequency error below counter resolution (0.1%).
- `python -m model.test_frontend_fixed build/sim/frontend/speech/*.flac`:
  dev-clean 2277-149896-0005, -0026, -0033; 559/491/283 frames.
  Guarded power-dB error max 0.107337/0.114086/0.108379;
  RMS 0.007010/0.007239/0.007376 dB. Guard=2^-24, matching PS log guard.
  Float comparison uses identical uncentered streaming framing; centered
  model/frontend.py has a documented timestamp/boundary difference.

Vivado OOC command:
`vivado.bat -mode batch -source sim/frontend/ooc.tcl`.
Vivado 2026.1 exits before synthesis: "valid license was not found".
LUT/FF/DSP/BRAM and WNS are therefore **unmeasured**; no timing pass claimed.
Current combinational multiport FFT favors registers and needs resource/timing
review on a licensed host before board integration.

WER adapter: `python sim/frontend/eval_fixed_wer.py`, using the model worker's
40-clip selection and identical streaming framing for float/fixed A/B.
WER is pending model dependencies, weights and extracted 40-clip dataset.
No unchanged-WER acceptance claim is made.

Additional `make -C sim/frontend test-control` passes independent AW/W, held read response, 1024-entry saturation/backpressure, microphone overflow counter and sticky W1C clearing.

Remaining verification: exhaustive AXI read,
WSTRB and IRQ-clear/coalescing checks; beamformer comparison against Python;
button debounce tests; microphone electrical timing and board validation.
Underrun counter is reserved zero because FIFO emptiness stalls processing.
Beam delay history requires 256 microphone samples to warm up.



## Platform

Real KV260 validation, 2026-11-01, Ubuntu 24.04 aarch64,
kernel 6.8.0-1036-xilinx. Build/integration/API details: [platform.md](../platform.md).

| Acceptance | Result |
|---|---|
|100 MHz routed setup/hold|PASS: WNS +4.373 ns, WHS +0.010 ns; zero failing endpoints|
|Placed resources|7,340 LUTs, 10,130 registers, 7.5 BRAM tiles, zero DSPs|
|Firmware packaging/deployment|PASS: bootgen bit.bin, dtbo, shell.json; xmutil stt-loopback loaded|
|Golden loopback + trailing sentinels|PASS: 16,17,31,127,4095,4096,65537,1048576,8388607,8388608 bytes|
|64 consecutive 8 MiB transfers|PASS: MM2S 1.562481 GB/s, S2MM 1.562481 GB/s, aggregate 3.124962 GB/s|
|Bad-address error and automatic reset|PASS; address 0x7000000000, next valid transfer bit-exact|
|Cache-maintenance comparison|Skipped sync produces stale data; flush/invalidate passes|
|Range sync with unequal TX/RX capacity|PASS: 100 x 255-byte packets in 256-byte RX capacity; actual BTT=255|
|Small-packet complete round trip|596.002 us mean, including four range-sync calls|
|30-minute IRQ soak|PASS: 1800.004622 s, 179998 IRQs, zero missing hardware periods and UIO gaps|
|GPIO register readback|LED 0/1/0/1; idle button 0; physical press not tested|
|Loaded-board idle power during soak|100 samples, nominal 0.1 s: mean 3.089 W, max 3.390 W|

Bandwidth measures simultaneous traffic on the coupled loopback. It includes
submission/wait and excludes cache preparation; isolated TX-only/RX-only rates
are not claimed. Power is idle with the timer running, not inference or DMA load.
The intentionally shared DMA register aperture yields a benign dtc duplicate
unit-address warning; all TX, RX, GPIO and timer UIO nodes bind on the board.
Raw loopback output: `results/platform/loopback-final.log`; timing/utilization reports are in the same directory.

ikwzm u-dma-buf (repository ikwzm/udmabuf), pinned commit
`2cdfd21bb3decb7be02ccf7b5d21c33c8b92b5b0`, builds with matching installed headers
and GCC 13. Two cacheable contiguous 16 MiB buffers have current physical addresses
0x4e400000 / 0x4f400000, dma_coherent=0, quirk_mmap_mode=2, open without O_SYNC.
Physical addresses are queried, never hardcoded. Native library/tests compile
with `-O2 -std=c11 -Wall -Wextra -Werror`. Whole-buffer write, device sync, CPU sync
and bytewise verification pass in 87.443 / 87.666 ms; this includes CPU work.

Exact PowerShell commands from the repository root:

```powershell
./hw/build.ps1 loopback
# Repeat packaging alone after a successful Vivado run:
./hw/package.ps1 loopback
./tools/deploy.ps1 loopback
./tools/board.ps1 put tools/setup-buffers.sh stt/setup-buffers.sh
./tools/board.ps1 run -Sudo 'sh /home/ubuntu/stt/setup-buffers.sh'
./tools/board.ps1 put sw/plio stt/
./tools/board.ps1 run -Sudo 'set -e; cd /home/ubuntu/stt/plio; make; ../build/sw/plio/tests/buffers; ../build/sw/plio/tests/loopback; ../build/sw/plio/tests/range; ../build/sw/plio/tests/gpio'
./tools/board.ps1 put tools/power.sh stt/power.sh
./tools/board.ps1 run 'sh ~/stt/power.sh 100 0.1'
./tools/board.ps1 put tests/platform/start-irq-soak.sh stt/start-irq-soak.sh
./tools/board.ps1 run -Sudo 'sh /home/ubuntu/stt/start-irq-soak.sh'
./tools/board.ps1 run 'cat ~/stt/irq-soak.log; cat ~/stt/irq-soak.exit'
```

Vivado synthesis/implementation, packaging and deployment were executed in steps;
the build script composes those same stages. Elevated host execution is needed
for the installed license and WSL access in the managed sandbox. No auth changes
or system startup files were installed; rerun setup-buffers.sh after reboot.

`hw/check_interfaces.tcl` successfully elaborates the reusable module helper
against the actual stt_frontend AXI-Lite and 32-bit samples/features interfaces.
This is interface elaboration, not front-end synthesis/timing acceptance.
DMA memory width stays 128 bits; plio_dma_start_sized supports separate source
length and destination capacity and plio_dma_received_bytes reports actual BTT.

Final soak started approximately 11:09:52 UTC, CPU 3, SCHED_FIFO priority 50.
The test uses live TCR0 phase to distinguish missing hardware periods from
PS/PL clock drift; it also rejects UIO gaps and service intervals over 15 ms.
Short qualification: 5.000147 s, 500 interrupts, zero UIO gaps and missing
hardware periods, maximum interval 10.073337 ms. No 30-minute pass claimed yet.
