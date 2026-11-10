# Live microphone and appliance results (2026-11-07)

The KV260 PMOD front end captures one MEMS I2S microphone. A Windows laptop
plays the LibriSpeech test clips through its built-in speakers (Realtek,
master volume 60 %, set with `tools/pc_volume.ps1 -Set 0.6`) towards that microphone, so every result below includes
the speaker, the room acoustics and the microphone. There is no injection.
The board ran `stt-full150` throughout. Evidence is in `results/mic/`.

## Which input carries the microphone

The tool `fetool mic -o` now dumps each PL mel frame. The PC played a 1 kHz
tone gated 1 s on / 1 s off, and each of the four inputs was recorded for 8 s.
The analysis looks for 0.5 Hz modulation in each band (`channel/result.txt`).

| Input | Mean band level | Strongest modulation | Verdict |
|---|---|---|---|
| I2S1 (pin 4), left slot | -70.8 dB | **17.8 dB in band 20 (1 kHz)** | microphone |
| I2S1, right slot | all-zero frames | none | the mic tristates SD in the other slot |
| I2S2 (pin 3), left | -45.5 dB | 1.2 dB | floating input |
| I2S2, right | -62.3 dB, 35 dB frame jitter | 6.7 dB (band 52, noise) | floating input |

The microphone is therefore `--mic 1`, left slot, which is the existing
default. The WS clock measures 99.998 frames/s (-0.002 %).

The front end's `MIC*_MEANSQ` statistic zero-extends negative samples before
squaring. That inflates the statistic for DC-negative signals, so the
statistic is unusable. It is diagnostic only and does not affect the feature
path. The mel dump is the measurement used instead.

## Signal and the fixes it required

These are the levels measured with speech playing (`offline/set10_v60.bin`,
104 s, 10 clips):

- **DC offset in band 0.** The microphone's DC offset (about -2,800 to -3,100 LSB of 18
  bits, -33 dBFS) leaks into mel band 0 at -33 dB. That made it the largest
  term in the old 64-band VAD energy (floor 6e-4 against the provisional
  threshold of 1e-2).
- **Noise floor.** With band 0 excluded, the floor is -43.0 dB with 0.27 dB
  standard deviation in silence.
- **Speech level.** Speech reaches -22 to -34 dB in 0.25 s averages. That is
  15–20 dB below the LibriSpeech level assumed by the running-normalisation
  prior, and many bands sit near the 2^-24 log guard.

The first live run used a fixed 64-band threshold (raised to 1.5e-3 for the test).
It fragmented phrases and dropped quiet words: 53.9 % WER on 3 clips, partly
because playback started before the app was listening. Three changes to `sttcap`
fix this. All of them apply to `--source pl-mic` only.

1. **Adaptive VAD.** It uses the energy of bands 1–63 against a
   minimum-statistics noise floor: the minimum of ten 50-frame block minima,
   covering 5 s. A frame counts as speech when it is ≥ `--vad-snr` dB above the
   floor (default 8) and ≥ `--mel-threshold` (default 2e-6). The first 0.3 s
   after start is ignored: a microphone start-up transient at frame 2 had created an empty phrase.
2. **`--mic-gain DB`** (default 20). It scales the band power before the log,
   which is exactly a digital gain ahead of the FFT.
3. **Valid JSON.** Mic-mode events wrote `"audio_time":nan`, which is invalid
   JSON for both the log and TCP clients. They now write `null`.

`--vad-log` now records, per frame, the energy, the floor and the active flag,
behind a 1 MiB buffer. In an earlier run the capture thread's unbuffered
4 KiB log writes coincided with a 0.8 s stall and 6 merges.

### Offline tuning

The tuning used the recorded PL mel frames of 10 clips (216 words), the float
QuartzNet and the same phrase policy (`offline/`). The sweep outputs are saved
in `offline/sweep_finals.txt` and `offline/sweep_running.txt`.

| Setting | Final WER (utterance norm) | Running-norm WER (preview proxy) |
|---|---|---|
| VAD 6 / 8 / 10 dB, gain 0 dB | 8.80 / 9.26 / 9.72 % | — |
| gain 0 / 10 / 20 / 25 / 30 dB, VAD 8 dB | 9.26 / 8.80 / 9.26 / — / 9.72 % | 15.74 / 14.35 / **13.43** / 13.43 / 13.89 % |
| band 0 replaced from band 1 + prior offset | −2 to +3 words, no consistent gain | 0.5–2.3 points worse |

Finals use utterance normalisation and are insensitive to gain. Previews start
each phrase from the dev-clean prior, so 20 dB of gain helps them. Replacing
band 0 did not help and was not adopted.

## Live acoustic results on the board

`tests/mic/mic_playlist.py` builds the playlist WAV and its JSON clip list.
`tests/mic/mic_acceptance.ps1` runs the installed appliance control script
(`stt-caption prepare/run/cleanup`) in the foreground on the board. The
`live10` development run used `tests/mic/mic_play.ps1` with the board-side
`tests/mic/mic_run.sh` instead. The PC
starts playback once the board reports `Listening`, and it receives the TCP
caption stream itself. Receipt and playback times come from the same PC clock.
Latency therefore spans the PC's speaker output, the acoustics, capture,
inference, emission and network delivery.

| Run | Clips / words | Final WER | Preview WER | End of clip → final on the PC (p50 / p95 / max) | Capture |
|---|---|---|---|---|---|
| `t40`: full test set, 387 s | 40 / 848 | **11.20 %** (95) | 22.52 % (191) | 1415 / 1807 / 1958 ms | 39,592/39,592 frames; 0 sequence errors, overruns, drops or merges |
| `live10`: dev build, 98 s | 10 / 216 | 9.26 % (20) | — | — | 11,263 frames, 0 errors; 6 merges in one 0.8 s stall (VAD log I/O, since buffered) |
| `service3`: through `systemctl start stt-caption` | 3 / 65 | 10.77 % (7) | — | — | clean |
| `smoke3`, `power3` | 3 / 65 | 7.69 %, 9.23 % | 20.0 %, 15.4 % | 1466, 1455 ms | clean |

For comparison, the same 40 clips injected digitally give a final WER of
4.60 % (39/848). On the 10-clip subset the injected WER is 5.56 % (12/216).
The acoustic path therefore costs about 4–7 points.

The remaining errors are acoustic confusions, mostly rare words ("icteosaurus",
"tawlingss", "measurein"). There were no phrase-boundary losses and no empty
or missing finals: every clip produced a final, and the run had 64 phrases.
The final latency is the 0.7 s VAD silence hangover plus finalisation. Most
clips are longer than 6.4 s, and their finals run on the CPU worker in about
0.75 s.

In `t40`, the TCP client connected after the start-up `Listening` event and
received the other 2,595 events. They are identical to the tail of the board's
`session.jsonl`.

### Other checks

- **Silence** (`silence60`). Over 62 s with no playback, the room noise
  produced 1 onset whose final was empty, so no text appeared. Capture was
  clean.
- **Power** (`power3`). The INA260 board input read 3.63 W mean and 4.30 W max
  during live captioning (380 samples). The T1 idle reading was 3.48 W.
- **Replay regression** (`replay_regress`). The release binary replayed the
  validated demo WAV (`pl-inject`). Its caption content is identical to the
  `final-pl-1` demo evidence: 194/194 events matched on phrase, state, preview,
  tentative, final, caption, segments and window (`tests/demo/caption_identity.py`).
  The microphone changes therefore leave the measured replay path unchanged.
- **Display.** The framebuffer snapshot (`service3/caption.ppm`) shows the
  three committed captions. The service released the text console during the
  session and rebound it after stopping.

## Appliance

The release is `stt-caption-20261107-<git>` and is described in
[product.md](../product.md).

- **Build and install.** `install.sh` passed on the board. It ran the native
  strict build and all four unit checks (stream, clock, caption, emit), built
  u-dma-buf at the pinned revision, verified the asset and firmware hashes,
  and installed under `/opt/stt-caption`.
- **Service.** `systemctl start stt-caption` loaded `stt-full150` over the
  boot-default `k26-starter-kits`, loaded the DMA buffers, and captioned the
  test speech (`service3`).
- **Not yet done.** Boot autostart (`systemctl enable`) has not been performed
  or tested. It needs the board owner's approval.

## Limitations

- Results depend on the speaker, the distance and the room. The laptop's
  built-in speakers have little bass, and the 20 dB gain default was tuned at
  this placement only.
- Preview WER through the microphone (22.5 %) is much higher than for
  injection (7.3 %). Previews normalise from the LibriSpeech prior on every
  phrase, and the speaker-and-microphone channel differs from that prior.
  Adapting the prior to the channel is the obvious next step.
- Preview latency is not reported for the microphone. Mic mode has no
  FIFO-anchored sample clock, so the app marks `latency_valid=false`. The
  unanchored estimate is window end → event at about 246 ms p50, comparable to
  injection.
- The button was not pressed physically. Pressing it ends the session, and
  under systemd that restarts a fresh session.
