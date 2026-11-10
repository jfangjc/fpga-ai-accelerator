# QuartzNet 15x5 INT8 acceleration: test method, time breakdown and PL/PS split

This covers model inference only: the encoder and CTC decoding. The audio front
end (I2S, FFT, mel) is in [frontend](frontend.md). It is the product model,
on the final `stt-full150` overlay (engine at 150 MHz), with our C runtime
(`engdrv` driver, `qnrt` CPU runtime, `sttcap`). It does not use ONNX Runtime
or PyTorch. Sources: [full150](full150.md), [engine](engine.md),
[engdrv](engdrv.md), [cpu](cpu.md), [t2](t2.md), [mic](mic.md).

## Test method

| Test | Input | Method |
|---|---|---|
| Bit-exactness | 558 golden layer vectors; whole-network windows | RTL simulation and the board must match the Python integer golden model byte for byte (`sim/engine` checks, I2) |
| Speed-up (Table VII) | one saved 3 s window, INT8 features `[64,300]` | 20 alternating CPU/PL pairs on identical input, after 2 warm-up pairs; every pair's 4,350 logit bytes and CTC IDs must match. CPU is `qnrt`, 4 threads, `-O3 -mcpu=cortex-a53`; PL is the complete `engdrv_run_network` call. Model and program loading are excluded. Median ratio. |
| Accuracy | 40 LibriSpeech test-clean clips (`model/testset_40.txt`), 848 words; INT8 scales from dev-clean | Final WER against float; CPU and PL finals must agree |
| In-app timing | the same 40 clips, paced injection (T1) and through the microphone | `compute_ms` of every preview and final in the session log; 2,539 previews on T1, 2,403 on the microphone run |

## Which part runs on the PL and which on the PS

| Part | Where | Notes |
|---|---|---|
| INT8 feature window `[64,T]` in DDR (input) | PS writes, **PL** DMA reads | |
| Prolog conv, 77 depthwise convolutions | **PL** | 16 channel x 16 frame depthwise unit |
| 94 pointwise convolutions, including the final 1024->29 layer | **PL** | 32 x 16 INT8 MAC array |
| 15 residual adds | **PL** | |
| BN (folded into weights), bias, ReLU and Q31 requantisation | **PL** | the requantiser clamps for ReLU |
| Layer sequencing and tensor allocation | **PL** | one RUN_NETWORK command per window of up to 640 frames. The graph (descriptor and routing table) is fixed in hardware, and intermediate activations never leave the chip. |
| Weights and per-channel parameters (QNP2 program, 20.0 MB, 186 records) | DDR, streamed by **PL** DMA every window | the PS maps it once; weight loading overlaps compute (0.57 ms of weight waits per window) |
| Logits `[29,T]` back to DDR | **PL** DMA | |
| Driver: build the command, 4 range cache syncs, start DMA, wait for the IRQ | PS | `engdrv` |
| Greedy CTC (argmax per frame, collapse repeats, drop blanks) and window stitching | PS | |
| Windows over 640 frames (finals over 6.4 s) | PS CPU INT8 worker by default (`--final-backend auto`) | or the PL per-layer fallback |
| CPU backend (A/B baseline and `BACKEND=cpu`) | PS | `qnrt`, NEON INT8, same arithmetic |

## Time breakdown

### One 3 s window: PL against CPU

Median of 20 pairs (`full150.md`):

| Stage | PL | CPU INT8, 4 threads |
|---|---|---|
| **Complete call** | **69.6 ms** | **218.6 ms** |
| ↳ engine compute | 68.7 ms (10.31 M clocks at 150 MHz) | — |
| ↳ cache maintenance | 0.7 ms | — |
| ↳ host encoding, copies, DMA and IRQ | about 0.2 ms | — |
| **Speed-up** | **3.14x** | 1x |

About 3.02 GMAC per window gives about 44 GMAC/s on the PL and about 13.8 GMAC/s on the CPU.

### Inside the engine (one 3 s window, 150 MHz)

| Engine time | Clocks | Time |
|---|---|---|
| Execution | 10,160,362 | 67.74 ms |
| Waiting for weights | 85,994 | 0.57 ms |
| Command reception and transitions | 62,457 | 0.42 ms |
| **Total** | **10,308,813** | **68.73 ms** |

By layer type, from the per-layer counters (11.13 M clocks in per-layer
mode, which has more per-command overhead than RUN_NETWORK):

| Layers | Clocks | Share |
|---|---|---|
| 94 pointwise | 5.84 M | 52% |
| 77 depthwise | 2.52 M | 23% |
| 15 residual adds | about 2.77 M (by difference) | 25% |

### In the live app (final build)

| Request | Backend | Compute p50 | Compute max |
|---|---|---|---|
| Preview, 3 s window every 120 ms | PL RUN_NETWORK | 69.4 ms | 69.7 ms |
| Final, phrase of 6.4 s or less | PL RUN_NETWORK | 49.5 ms | 136 ms |
| Final, phrase over 6.4 s | CPU INT8 worker | 743 ms | 799 ms |
| Preview with the CPU backend (3 threads) | CPU | 280.4 ms | — |

CTC decoding, stitching and dispatch add well under 1 ms per request; queueing
before the engine is 0.08 ms at p50. On the PL backend, `sttcap` uses 10.7% of one core;
on the CPU backend it uses 274.6% and skips 1,220 preview updates because it
can't keep up.

### Whole clip at once (offline)

CPU INT8 runtime, 4 threads, per-clip median over the 40 clips (`cpu.md`):

| Stage | p50 per clip |
|---|---|
| Encoder | 547 ms |
| CTC decoding | negligible |

QuartzNet has no decoder network: greedy CTC is an argmax per frame, so the
PL encoder is almost all of the model's work.
