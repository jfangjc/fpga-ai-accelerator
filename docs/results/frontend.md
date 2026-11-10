# Frontend implementation results (2026-11-01)

Resource and functional acceptance passes. Architecture acceptance is partial:
32-bit exact FFT arithmetic and serial butterflies are retained, and full
per-stage module decomposition remains outstanding (see frontend_spec.md).

## Post-route implementation

Vivado 2026.1, xck26-sfvc784-2LV-c, 100 MHz, OOC synth_design / opt_design /
place_design / route_design. Clock XDC is read before synthesis; HD.CLK_SRC is
BUFGCE_X0Y0. Final run ended 2026-11-01 22:21:34 Sydney time.

| Metric | Routed result | Limit | Result |
|---|---:|---:|---|
| LUT | 3,026 | 6,000 | PASS |
| FF | 1,629 | 8,000 | PASS |
| DSP48E2 | 7 | 12 | PASS |
| BRAM36 equivalents | 6 | 12 | PASS |
| URAM | 0 | 0 | PASS |
| WNS | +2.594 ns | >=0 | PASS |
| Setup failing endpoints | 0 / 5,845 | 0 | PASS |
| WHS | +0.006 ns | >=0 | PASS |

Command (normal user / escalated shell):
`C:\AMDDesignTools\2026.1\Vivado\bin\vivado.bat -mode batch -source sim/frontend/ooc.tcl -log build/sim/frontend/vivado.log -journal build/sim/frontend/vivado.jou`.
Licence works: Common 17-3922 reports valid BASIC through 15-Sep-2027.
Reports/checkpoint are regenerated and ignored, not committed.

## Tests

Command: WSL FedoraLinux-44, `make -C sim/frontend check`.
Default Python is the repository `.venv/bin/python`; override PYTHON with an
absolute path. Final target exits zero and prints `PASS: all frontend checks`;
any failed dependency causes a FAIL summary and nonzero exit.

| Test | Result / count |
|---|---|
| Strict Verilator --lint-only -Wall | PASS, zero warnings, no global waivers |
| DSP against golden model | PASS, 200 real speech frames / 12,800 bands |
| Full AXI injection chain, random input/output stalls | PASS, 200 speech frames / 12,800 bands per run |
| IRQ period 1 with AXI W1C | PASS, 200 frames / 200 IRQs |
| IRQ period 3 with AXI W1C | PASS, 200 frames / 66 IRQs |
| Beam vs Python, both mics | PASS, 4 configurations / 3,072 samples after warmup |
| I2S both mics/channels | PASS, 1,024 BCLK rising edges in 1 ms (1.024 MHz) |
| Independent AW/W, held read response | PASS |
| FIFO capacity/backpressure | PASS, 1,024 entries |
| Mic overrun counter, sticky overflow, W1C | PASS |
| Underrun policy | PASS, empty FIFO stalls; reserved count remains zero |
| Button glitch rejection/debounce/sticky/IRQ/W1C | PASS |
| LED register set/clear | PASS |
| Injection pacing | PASS, 20 beats at exactly 6,250 clocks, 20 bulk beats at one clock |
| Full-scale overflow assertions | PASS, 5 patterns / 15 frames, signed32 checked at every butterfly |

The input driver holds TVALID/data until accepted. Speech source is
2277-149896-0005.flac, first 32,160 samples, from the dev-clean archive.
The DSP run uses 38,928,781 cycles for 200 frames; full-chain runs use
38,928,590 cycles. Average is about 194,644 cycles/frame, below 1,000,000.
Beam configurations (delay1, delay2, signed Q15 gain): (0,0,16384),
(1,5,-8192), (17,63,32767), (255,128,-32768). History is warmed for 256 samples.

## Numerical accuracy

Direct reference: `model/frontend.py:mel_power`, aligned by dropping its leading
centered boundary frame (160-sample center offset), and taking exactly the valid
streaming frame count. Guard is 2^-24, error = 10*log10((fixed+guard)/(float+guard)).

| Speech clip | Frames | Max absolute dB | RMS dB |
|---|---:|---:|---:|
| 2277-149896-0005 | 559 | 0.10733842 | 0.00701041 |
| 2277-149896-0026 | 491 | 0.11408368 | 0.00723880 |
| 2277-149896-0033 | 283 | 0.10817776 | 0.00737535 |
| 0005 at 1/100 amplitude (integer quantized) | 559 | 0.05231791 | 0.00514423 |

All satisfy max <=0.25 dB and RMS <=0.02 dB. No arithmetic change was made to
injected-sample mel. Beam DC/gain products were widened to fix old truncation.

## WER

Command: `.venv/bin/python sim/frontend/eval_fixed_wer.py`.
PASS: 40 selected test-clean clips, 848 reference words, 41 errors:
float WER = fixed WER = 4.83490566%, identical transcripts on all 40 clips.
This is a matched streaming-framing comparison; it excludes centered reflected
boundary frames for both arms and is not a reproduction of the centered baseline.
The initial temporary Python environment lacked torch; the repository model
virtualenv contains it and completed the evaluation.

## Interfaces and architecture limits

No port, register-map or DMA packet-format changes. FIFO now inserts a synchronous
read refill cycle. Disabling still pauses; reset starts an independent clip.

Completed: registered-read FFT/history/mel/band/FIFO/delay memories; circular
history; symmetric Hann; sparse per-bin triangular weights; shared registered
16x16 partial-product DSP; standalone beamformer and FFT RAM modules; explicit
state enum/counter widths; strict lint; corrected beam intermediate widths.

Not completed: requested <=27-bit scaled FFT and butterfly every 1-2 cycles;
the design retains exact signed32 FFT/Q22 arithmetic and schedules four serial
products per butterfly. Separate module files for AXI regs, FIFO control, source
mux, preemphasis, framer, window ROM, packet packer and button/LED remain to be
extracted from the top/scheduler. Therefore this is not full architecture acceptance.

## Changed files

- RTL: stt_frontend.sv, stt_i2s.sv, stt_fft_mel.sv; added stt_fft_ram.sv,
  stt_serial_mul.sv, stt_beamformer.sv, .gitignore, hann_half.mem, mel_sparse.mem;
  removed unused dense mel.mem and full hann.mem.
- Model: frontend_fixed.py, test_frontend_fixed.py.
- Simulation: Makefile, .gitignore, ooc.tcl, ooc.xdc, tb.sv, tb_top.sv,
  tb_control.sv, vectors.py, eval_fixed_wer.py; added beam_vectors.py, tb_beam.sv.
- Documentation: docs/frontend_spec.md, docs/results/frontend.md.

Only assigned paths were edited/staged. docs/results.md was not edited.
