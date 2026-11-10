# Results — live FPGA-accelerated speech-to-text on the KV260

Consolidated by the senior engineer. Every number below was produced on the real
KV260 or by the bit-exact simulation flow, and links to the per-area report that
holds the exact commands and raw evidence. Testing injects audio into the PL after
the I2S FIFO (proposal Fig. 5 dashed path); no physical microphone was used.

Final configuration: **`stt-full150`** — PL front end (I2S ×2, FIFO, beamformer,
FFT/mel) at 100 MHz plus the INT8 engine (32×16 MAC array, 16×16 depthwise unit,
requantisers, on-chip RUN_NETWORK) and its DMA at 150 MHz; CPU runtime, driver and
`sttcap` captioning app on the A53s.

## Proposal Table VII — success metrics (final configuration)

| Metric | Measured | Core target | Stretch target | Source |
|---|---|---|---|---|
| Speed-up vs the same model in INT8 on the CPU | **3.14×** — 3 s window 69.5 ms PL vs 218.2 ms CPU (20 paired windows, identical INT8 features); 2.09× with the 100 MHz `stt-full` | ≥ 2× ✅ | ≥ 3× ✅ | [full150](results/full150.md), [t2 evidence](../results/t2/extras/paired-20.txt) |
| Preview latency p95 (age of the oldest new audio at display, incl. queueing) | **204.9 ms** on T1 (p50 204.3, max 208.2; 40 clips, 0 merged updates) and **209.0 ms** across the 30 min soak (max 217.0, 0 merged updates, ≤ 209.3 ms in every 5 min block) | ≤ 1000 ms ✅ | ≤ 250 ms ✅ | [t1](results/t1.md), [t2](results/t2.md) |
| WER, finals | **4.60 %** (39/848 words; float 4.36 %) | ≤ 10 % ✅ | INT8 ≤ float + 1 ✅ | [t1](results/t1.md), [model](results/model_platform.md) |
| Board power | **3.68 W mean, 4.60 W max** while captioning T1 clips (idle 3.48 W); **4.10 W mean, 5.18 W max** over the 30 min soak, including CPU-worker finals | ≤ 20 W ✅ | ≤ 15 W ✅ | [t2](results/t2.md) |
| PS load | `sttcap` 10.7 % of one A53 (PL backend, T1 clips) vs 274.6 % with the CPU backend; 46.2 % mean over the 30 min soak, where long finals run on the CPU worker | reported | — | [t2](results/t2.md) |
| Reach goal: Moonshine Streaming Tiny on the engine | Done in the separate `moonshine` repository: encoder linears bit-exact on the engine, but no end-to-end speed-up (the PS decoder dominates) and streaming not real-time, so the product uses QuartzNet | — | — | `moonshine` repository |

Also measured: word-appearance latency L_word p50/p95 **604/664 ms** (PL; CPU 977/1179;
the 0.48 s lookahead dominates — Python predicted 600/660); committed-preview WER
7.31 % (PL) vs 6.84 % predicted by the Python streaming simulation (62 vs 58 errors
on the same 2,539 updates, from C-vs-Python float front-end ±1-frame window
bookkeeping; finals unaffected).

## Integration and system tests (proposal Table VIII)

| Test | Result | Source |
|---|---|---|
| Unit tests (Table IX) | All subsystems pass their unit tests — see the subsystem table below | — |
| I1 — WAV replay → DDR features match the unit reference | 6 clips, bulk + 16 kHz paced: 9,132 frames / 584,448 mel bands bit-exact; frame-interval p99 within +100 µs of 10 ms; front-end latency ≈ 2.5 ms | [i1](results/i1.md) |
| I2 — saved chunks through the engine → CTC identical to the CPU integer reference | Per-layer (old engine) 10/10; RUN_NETWORK 13/13 windows (69,136 logit bytes) on `stt-full`, 12/12 on `stt-full150`, incl. 640/656-frame boundaries and 1552-frame fallback | [i2](results/i2.md), [full150](results/full150.md) |
| I3 — engine outputs → golden transcript on screen | 6 clips: all 70,528 logit bytes = golden; PL = CPU byte-identical; finals to framebuffer + TCP | [app](results/app.md) |
| T1 — live latency, WER, A/B speed-up | 40 clips, paired CPU/PL on the same bitstream; all core targets met (100 MHz) and all stretch targets met (150 MHz) | [t1](results/t1.md) |
| T2 — power, 30 min soak, demo rehearsal | **Pass — no dropped samples.** Final-configuration soak (`3f124f5`): 180,079/180,079 frames with every error/overrun/underrun/drop/lost-stitch counter at 0; 15,262/15,262 TCP events; 0 merged previews; preview p50/p95/max 208.0/209.0/217.0 ms with no drift; all 196 finals identical to the first soak; power 4.10 W mean / 5.18 W max; RSS +56 KiB after warm-up in six small steps, then flat for the last 10 min. Demo rehearsal (`tests/demo/demo.ps1`) on the final build after a fresh deploy and quick I2: PL ×2 (194/194 TCP events each, 0 merges) and CPU-only fallback ×2 (106/106; 98/102 with the client joining by snapshot 0.76 s into replay) passed with framebuffer, TCP, SD log and PPM evidence; all four end on an identical caption. Fixes found by the soak: the sample-clock fit (the earlier p95 drift was a ~-20 ppm anchoring artefact), long finals moved to a CPU worker with phrase-indexed caption emission (506 → 0 merges), and static front-end scratch (308 KiB heap step removed). The project's extra `strict_t2_pass` check is still false, only because its RSS endpoint must be ≤ 0 KiB and the soak ends at +56 KiB | [t2](results/t2.md) |
| Live microphone (acoustic; beyond Table VIII) | PC speaker → PMOD I2S MEMS mic (I2S1, left slot) → full pipeline, 40 clips / 848 words: final WER 11.20 % (4.60 % injected), preview WER 22.52 %; end of clip → final on the PC p50/p95 1.42/1.81 s; 39,592/39,592 frames with 0 errors, overruns, drops or merges; all 2,595 TCP events match the board log; 3.63 W mean / 4.30 W max. Replay through the release binary is caption-identical to the validated demo (194/194 events) | [mic](results/mic.md) |
| Appliance | `stt-caption` release: `install.sh` runs a native strict build and the unit checks and installs to `/opt/stt-caption`. A systemd service prepares the overlay and buffers, releases the console, writes SD session logs, and treats the button as a new session; a service run captioned live speech. Boot autostart is not yet enabled | [product](product.md), [mic](results/mic.md#appliance) |

## Subsystems (proposal Table IX unit tests)

| Subsystem | Key results | Source |
|---|---|---|
| Model / golden | QuartzNet 15×5 from NGC; INT8 per-channel weights, Q31 single-rounding requant; float 4.36 % / INT8 4.60 % WER; 558 golden layer vectors | [model](results/model_platform.md), [timing and PL/PS split](results/quartznet.md) |
| DSP front end (PL) | I2S ×2 at 1.024 MHz, 1024-sample FIFO, beamformer, iterative FFT + sparse mel; bit-exact vs Python; 3,026 LUT, 7 DSP, WNS +2.594 ns @100 MHz; log-mel error ≤ 0.114 dB; WER unchanged | [frontend](results/frontend.md) |
| Accelerator (PL) | 512-MAC output-stationary array + 256-MAC depthwise on DSP48E2, requantiser, residual add, RUN_LAYER and RUN_NETWORK (186 records on chip, T_in ≤ 640); 558/558 vectors bit-exact; 3 s window 10.31M cycles; OOC 46.3k LUT, 836 DSP, 60 URAM, closes 100 MHz (+1.181 ns) and 150 MHz (+0.292 ns) | [engine](results/engine.md) |
| Data interface | AXI DMA SG, 1.57 GB/s/direction, bad-transfer recovery; QNC2 parser/packer with malformed-packet recovery | [platform](results/model_platform.md), [engine](results/engine.md) |
| PS–PL I/O | uio + u-dma-buf range cache maintenance (stale-read test), IRQ-driven capture (30 min, 0 missed), engdrv cosim + board transports, RUN_NETWORK with ~0.9 ms host overhead per window | [platform](results/model_platform.md), [engdrv](results/engdrv.md) |
| CPU INT8 runtime (A/B baseline + fallback) | NEON, 4 threads: 13.1 GMAC/s (64 % of measured peak), 3 s window 217 ms; 40-clip RTF 0.080, WER 4.36 % | [cpu](results/cpu.md) |
| Streaming policy | 140-configuration sweep; chosen W = 3 s, R = 0.48 s, silence padding, 120 ms hop | [streaming](results/streaming.md) |
| Application | Multi-threaded `sttcap`: Fig. 3 caption states, VAD, stitching, full-phrase finals (long phrases on a separate CPU worker), phrase-indexed caption emission (previews never wait for a pending final), framebuffer captions, TCP server with late-join snapshots, SD log, LED/button, PL-clock-fitted latency instrumentation | [app](results/app.md) |

## Stretch features (proposal Table V)

| Feature | Status |
|---|---|
| Beamforming | PL delay-and-sum implemented and bit-exact. Two-mic noisy-test simulation (40 clips × white/babble/directional × 20–0 dB): pooled WER 43.6 % single mic → 31.6 % beam, clean unchanged; spatial gain 3.0 dB (uncorrelated), 0.7–1.8 dB (directional). Kept for the dual-mic default ([beamformer](results/beamformer.md)). Not tested with two physical microphones; one is fitted ([mic](results/mic.md)). |
| Button, LED | LED follows the Speaking state; debounced sticky button stops capture; register-level tests on the board (no physical press). |
| SD logging | JSON-lines session log + PPM caption snapshots, written off the latency path. |
| TCP | Line-delimited JSON caption server with late-join snapshots; PC client `tools/caption_client.py`. |

## Known limitations

- Table VII metrics come from injecting audio into the PL after the I2S FIFO
  (the proposal's A/B bench path). The live microphone was tested separately:
  laptop speaker to microphone gave 11.2 % final WER. Preview latency is not
  anchored in microphone mode, and the button was not pressed physically.
- Preview quality: committed-preview WER 7.31 % vs the 6.84 % Python prediction
  (C front-end float differences); finals are identical across backends.
- Final latency: phrases longer than 640 frames (173 of 196 in the soak) are too
  long for on-chip RUN_NETWORK, so their finals run on the CPU worker. That keeps
  the engine free for previews, but speech-end-to-final-display p50/p95 is
  1.65/2.30 s (1.25/1.35 s when long finals ran per-layer on the engine and
  blocked previews). Finals are not a Table VII metric; preview latency is.
- Memory: RSS is bounded but not exactly flat. It rises 56 KiB in small steps
  during the first 20 minutes of the soak and then stays flat; the steps were not
  traced to a single allocation.
- `stt-full150` leaves 0.282 ns of setup margin on the 150 MHz domain.
