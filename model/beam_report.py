"""Validate a completed beam_study run and render its reviewable Markdown report."""
import argparse
import json
from pathlib import Path

from beam_study import summarize


def render(root, require_40=True):
    summary = json.loads((root/'summary.json').read_text())
    config = summary['config']
    rows = [json.loads(line) for line in (root/'clips.jsonl').read_text().splitlines()]
    ids, delays = config['ids'], config['delays']
    assert summary['complete']
    assert not require_40 or len(ids) == 40
    conditions = [('clean', 100)] + [(k, s) for k in config['kinds'] for s in config['snrs']]
    expected = {(uid, k, s, d) for uid in ids for k, s in conditions for d in [None]+delays}
    actual = {(r['id'], r['kind'], r['snr'], r['delay']) for r in rows}
    assert len(rows) == len(expected) and actual == expected, 'incomplete or duplicate captures'
    assert 0 in delays, 'the default-setting gate needs the broadside result'
    by_key = {(r['id'], r['kind'], r['snr'], r['delay']): r for r in rows}
    for uid in ids:
        for kind, snr in conditions:
            single = by_key[uid, kind, snr, None]
            for d in delays:
                b = by_key[uid, kind, snr, d]
                assert (b['reference'], b['words'], b['scale'], b['noise_ids']) == (
                    single['reference'], single['words'], single['scale'], single['noise_ids'])
                assert b['signal_energy'] == single['filtered_signal_energy'], 'target misalignment'
        clean = [by_key[uid, 'clean', 100, d] for d in delays]
        assert len({r['hyp'] for r in clean}) == 1, 'clean steering outputs differ'
    assert summarize(rows) == summary['conditions'], 'stored summary disagrees with captures'
    groups = {(g['kind'], g['snr'], g['delay']): g for g in summarize(rows)}
    for g in groups.values():
        assert g['clips'] == len(ids)
    def wer(g):
        return f"{g['wer']*100:.2f}"
    single_errors = sum(r['errors'] for r in rows if r['kind'] != 'clean' and r['delay'] is None)
    beam_errors = sum(r['errors'] for r in rows if r['kind'] != 'clean' and r['delay'] == 0)
    words = sum(r['words'] for r in rows if r['kind'] != 'clean' and r['delay'] is None)
    keep = beam_errors < single_errors
    clean_single, clean_beam = groups['clean', 100, None], groups['clean', 100, 0]
    text = f'''# Beamformer simulation study (2026-11-05)

## Decision

**{'Keep the beamformer enabled for the simulated dual-microphone default' if keep else 'Leave the beamformer disabled by default'}.**
Apply the proposal's noisy-test WER gate to the broadside default, pooling the
three noise types and four SNRs with equal numbers of clips. Single mic:
{100*single_errors/words:.2f}% ({single_errors}/{words}); broadside beam:
{100*beam_errors/words:.2f}% ({beam_errors}/{words}).
This is a recommendation; no driver, RTL, or default configuration was modified.
The clean control is {wer(clean_single)}% ({clean_single['errors']}/{clean_single['words']})
for single mic and {wer(clean_beam)}% ({clean_beam['errors']}/{clean_beam['words']})
for the beam; the noisy-test gate does not imply unchanged clean-speech WER.

## Corpus and method

All {len(ids)} fixed `model/testset_40.txt` LibriSpeech test-clean clips are used in
every condition. Reference transcripts remain unchanged. WER is corpus word edit
distance divided by reference words, never the mean of per-clip WERs. Every cell
below has {len(ids)} clips. There are {len(rows)} recognition captures, including clean controls.
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
delays. Seed: `{config['seed']}`. Short noise clips are periodically repeated.

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

'''
    text += '| Noise | Input SNR (dB) | Single mic | ' + ' | '.join(f'Beam d={d}' for d in delays) + ' |\n'
    text += '|---|' + '---:|' * (len(delays)+2) + '\n'
    for kind, snr in conditions:
        line = [kind, 'clean' if kind == 'clean' else str(snr), wer(groups[kind, snr, None])]
        line += [wer(groups[kind, snr, d]) for d in delays]
        text += '| ' + ' | '.join(line) + ' |\n'
    text += '''
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
'''
    for kind, snr in conditions[1:]:
        single = groups[kind, snr, None]
        beams = [groups[kind, snr, d]['snr_db'] for d in delays]
        def span(values):
            return f'{min(values):.2f}–{max(values):.2f}'
        text += (f"| {kind} | {snr} | {single['snr_db']:.2f} | {span(beams)} | "
                 f"{span([v-single['snr_db'] for v in beams])} | "
                 f"{span([v-single['filtered_snr_db'] for v in beams])} |\n")
    text += '''
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

Model SHA256: `''' + config['hashes']['data/int8.pkl'] + '''`.

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
'''
    return text


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path)
    p.add_argument('--out', type=Path)
    p.add_argument('--allow-partial', action='store_true', help='for smoke-test reporting only')
    args = p.parse_args()
    report = render(args.root, require_40=not args.allow_partial)
    if args.out:
        args.out.write_text(report)
    else:
        print(report)
