# Beamformer simulation study (2026-11-05)

## Decision

**Keep the beamformer enabled for the simulated dual-microphone default.**
Apply the proposal's noisy-test WER gate to the broadside default, pooling the
three noise types and four SNRs with equal numbers of clips. Single mic:
43.63% (4440/10176); broadside beam:
31.62% (3218/10176).
This is a recommendation; no driver, RTL, or default configuration was modified.
The clean control is 5.19% (44/848)
for single mic and 5.19% (44/848)
for the beam; the noisy-test gate does not imply unchanged clean-speech WER.

## Corpus and method

All 40 fixed `model/testset_40.txt` LibriSpeech test-clean clips are used in
every condition. Reference transcripts remain unchanged. WER is corpus word edit
distance divided by reference words, never the mean of per-clip WERs. Every cell
below has 40 clips. There are 2600 recognition captures, including clean controls.
The same clips recur across conditions; these are not independent test corpora.

Target arrivals are mic1 at sample 0 and mic2 at samples 0, 1, 2, or 3 (16 kHz).
These integer delays approximate broadside through endfire at roughly 6 cm spacing.
Steering is known exactly from synthesis, without direction estimation or tuning
on recognition results. White noise uses independent pseudorandom streams per
microphone. Babble is a sum of eight independently offset, RMS-normalized dev-clean
utterances per microphone, from disjoint speaker sets. The directional interferer
is one other dev-clean speaker, arriving at mic1 three samples later than mic2
(relative delay -3), separated from every tested target direction. No dev-clean
noise utterance is taken from the test-clean target set. Noise source IDs are saved
per clip. Speech/noise waveforms and random offsets repeat across SNRs and steering
delays. Seed: `20261105`. Short noise clips are periodically repeated.

Noise RMS is set relative to whole-clip target RMS before quantization. A common
preprocessing step mean-centers target speech and each dev-clean source utterance
before mixing; white noise retains its finite-record mean. A common
capture scale for both microphones and all steering settings in a clip/noise/SNR
task bounds each mixture to 0.20 full scale; scale varies across tasks to prevent
clipping. This deliberately avoids saturation and does not model real microphone
noise floors. Conversion is nearest-even rounding of amplitude times 131072 to
signed 18-bit two's-complement samples; no dither. Capture and beam-output bounds
are asserted. There are 512 leading zero samples and 16 trailing zeros; reset is
modeled separately for every capture. The full speech is retained after removing
warmup and compensating the known beam latency, without tail mel padding.

The single-mic baseline is actual mic1 bypass into the same fixed-point mel path.
The beam calls `frontend_fixed.beamform_fixed` directly, including each channel's
DC filter, 18-bit history wrapping, integer delays, signed Q15 multiply, floor
shifts, and signed18 output wrapping. The single-mic bypass has no DC filter in
hardware; the SNR table also supplies a matched-DC control to isolate spatial gain.
Every recognition then calls `mel_power_fixed`, `log_normalize(mode='utterance')`,
INT8 input quantization using `data/int8.pkl` input scale, `golden_int8.network`
with true unpadded frame length (features padded to 16), and greedy CTC.
No float-network inference, centered/reflected STFT, or running normalization is used.

## WER (%)

`d` is the physical target arrival delay at mic2 relative to mic1; the beam is
steered to that delay. Single mic is shared across the four steering comparisons.

| Noise | Input SNR (dB) | Single mic | Beam d=0 | Beam d=1 | Beam d=2 | Beam d=3 |
|---|---:|---:|---:|---:|---:|---:|
| clean | clean | 5.19 | 5.19 | 5.19 | 5.19 | 5.19 |
| white | 20 | 6.01 | 5.07 | 5.42 | 6.25 | 6.60 |
| white | 10 | 10.14 | 8.84 | 7.78 | 8.96 | 7.78 |
| white | 5 | 21.82 | 12.62 | 12.85 | 13.56 | 12.38 |
| white | 0 | 43.16 | 29.83 | 30.78 | 28.89 | 28.54 |
| babble | 20 | 6.37 | 5.07 | 5.19 | 4.36 | 5.19 |
| babble | 10 | 35.26 | 12.03 | 11.67 | 12.03 | 11.91 |
| babble | 5 | 63.92 | 28.30 | 28.07 | 27.12 | 25.35 |
| babble | 0 | 90.21 | 61.67 | 61.79 | 60.97 | 59.79 |
| directional | 20 | 11.56 | 7.55 | 7.31 | 7.31 | 7.31 |
| directional | 10 | 67.45 | 52.24 | 50.35 | 49.06 | 46.93 |
| directional | 5 | 80.31 | 73.70 | 71.70 | 73.47 | 68.51 |
| directional | 0 | 87.38 | 82.55 | 82.78 | 83.37 | 81.25 |

## Measured output SNR

For each quantized noisy capture, process its quantized clean target through the
same path; residual = noisy output minus clean output. SNR is
`10 log10(sum(clean^2)/sum(residual^2))`, pooled over all 40 clips after latency
alignment. Residual includes quantization and fixed-point rounding interactions.
The matched-DC single control duplicates mic1 into both beam inputs with registers
1,1 and Q15 gain 16384, implementing the same DC and unity gain. Spatial gain is
beam SNR minus this control's SNR; total gain compares against actual mic1 bypass.
Ranges cover all four steering delays, with no averaging in dB.

| Noise | Input SNR | Measured mic1 SNR | Beam SNR range | Total gain range | Spatial gain range (matched DC) |
|---|---:|---:|---:|---:|---:|
| white | 20 | 20.00 | 22.99–23.00 | 2.99–3.00 | 3.00–3.01 |
| white | 10 | 10.00 | 13.00–13.01 | 3.00–3.01 | 3.01–3.01 |
| white | 5 | 5.00 | 8.01–8.01 | 3.01–3.01 | 3.01–3.01 |
| white | 0 | -0.00 | 3.01–3.01 | 3.01–3.01 | 3.01–3.01 |
| babble | 20 | 20.00 | 23.04–23.04 | 3.04–3.04 | 3.01–3.01 |
| babble | 10 | 10.00 | 13.05–13.05 | 3.05–3.05 | 3.02–3.02 |
| babble | 5 | 5.00 | 8.06–8.06 | 3.06–3.06 | 3.02–3.02 |
| babble | 0 | 0.00 | 3.06–3.06 | 3.06–3.06 | 3.02–3.02 |
| directional | 20 | 20.00 | 20.83–21.82 | 0.83–1.82 | 0.79–1.79 |
| directional | 10 | 10.00 | 10.80–11.79 | 0.80–1.79 | 0.76–1.75 |
| directional | 5 | 5.00 | 5.78–6.76 | 0.78–1.76 | 0.74–1.72 |
| directional | 0 | 0.00 | 0.76–1.73 | 0.76–1.73 | 0.72–1.69 |

Two equal, independent noise powers average to half the single-channel power:
about 3.01 dB. Finite-record correlation can place a measured result slightly
above or below that value. The DC filter changes spectral weighting, so total
gain need not equal spatial gain. A coherent second speaker is not independent
noise: a small two-mic aperture provides frequency-dependent attenuation rather
than a broadband null. The directional rows must be judged by WER as well as SNR.

## Register recommendation and latency

- Common per-channel gain: `16384` (`0x4000`, signed Q15 = 0.5). The two channels
  are summed and multiplied by this single shared gain; there are no separate
  gain registers. This gives unity gain for an aligned target.
- Broadside: delay1=1, delay2=1; `BEAM @ 0x20 = 0x40000101`.
- Mic2 target lag d=0,1,2,3: delay1=d+1, delay2=1; respectively
  `0x40000101`, `0x40000102`, `0x40000103`, `0x40000104`.
  For a target lagging at mic1, swap the two delay fields.
- In a dual-microphone configuration, source=sum and enable gives
  `CONTROL @ 0x00 = 0x00000005`, before adding any required channel/IRQ flags.
  Mic1-only bypass uses `0x00000001`. Stop before changing sources/delays;
  reset between independent clips and warm history for at least 256 samples.
- For this project's injected-audio demo, replay the precombined beam samples
  using injection source: `CONTROL=0x00000007` bulk or `0x0000000f` paced.
  Selecting sum without physical mic streams cannot replace this injection path.

Delay register zero is a read-before-write ring-buffer case: the Python model
reads the entry last written 256 samples earlier, then the stored DC state adds
one sample. It is not zero-latency bypass. For nonzero registers the aligned
target latency here is d+2 samples, confirmed by impulse checks for d=0..3.
Use the nonzero settings above. The microphone channel must match the SEL pin.

The single-stream AXI injection source bypasses microphone source selection and
cannot present a two-channel capture to the hardware beamformer. This experiment
synthesizes and combines the two channels in the verified Python model; generated
beam outputs can be replayed into injection to test the shared downstream path.

## Commands, artifacts, and verification

From PowerShell at the repository root:

```powershell
wsl -d FedoraLinux-44 --cd /mnt/c/Users/jfang/Documents/stt-accelerator -- env OPENBLAS_NUM_THREADS=1 .venv/bin/python model/beam_study.py --self-check
wsl -d FedoraLinux-44 --cd /mnt/c/Users/jfang/Documents/stt-accelerator -- env OPENBLAS_NUM_THREADS=1 .venv/bin/python model/beam_study.py --workers 12 --save-audio
wsl -d FedoraLinux-44 --cd /mnt/c/Users/jfang/Documents/stt-accelerator -- env OPENBLAS_NUM_THREADS=1 .venv/bin/python model/beam_report.py data/beam_study --out docs/results/beamformer.md
wsl -d FedoraLinux-44 --cd /mnt/c/Users/jfang/Documents/stt-accelerator/rtl/frontend -- bash -lc 'test -s ../../sim/frontend/beam.mem && ../../sim/frontend/obj_beam/Vtb_beam'
```

The last command reruns the existing compiled Verilator test and requires its
existing vectors; it passed four gain/delay configurations and 3,072 bit-exact
samples after warmup. For a fresh rebuild, the existing repository command is
`make -C sim/frontend test-beam` in WSL (it regenerates simulation artifacts).
The self-check passed d=0..3 impulse alignment and the zero-delay ring case;
its separate independent-white-noise check measured 2.9726 dB suppression.

`data/beam_study/config.json` records configuration and SHA256 hashes;
`clips.jsonl` records hypotheses, references, edits, words, source IDs, scale,
energies and inference time; `summary.json` records all condition aggregates.
`audio/*.wav` contains each single/beam output in PCM24, preserving signed18
samples exactly. All generated data/audio are gitignored. The study resumes
completed clip/noise/SNR tasks when rerun with the same configuration. A changed
script or model requires a fresh `--out`. The report validates a complete unique
Cartesian product of clips, conditions and steering settings before rendering.

Model SHA256: `afe4b44f6696523d7de826f9bcf355e4e80b12062ddd4deff78bb84bdd9c2519`.

## Limits of the decision

This establishes a simulation-only noisy-finals WER result for this fixed 40-clip
test set. It does not establish acoustic performance on a KV260. There are no
physical microphones, room impulse responses, reflections/reverberation, mismatch
in sensitivity or phase, fractional arrival delays, clock skew, motion, diffuse
spatially correlated noise, or clipping. The babble streams are statistically
independent across microphones by construction, unlike a shared room sound field.
Short dev-clean noises loop, which can introduce periodic boundaries. Steering is
an oracle; no direction-finding or steering-error robustness was measured. Only
one interferer direction and one deterministic noise realization per target/type
were tested. No inference about significance on a larger corpus is claimed.

The hardware arithmetic path has a Verilator bit-exact check against Python;
that check is distinct from board I2S timing/electrical validation and from this
simulated acoustic model. Utterance normalization matches final captions only;
live preview/running-normalization WER and latency were not evaluated. No proposal
overall WER, real-time, or board acceptance criterion is claimed by this study.
