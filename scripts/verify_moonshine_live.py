"""Replay the exact FPGA update boundaries through the integer oracle.

Backlog coalescing is timing-dependent: an independently paced oracle would
choose different partial boundaries. This verifier uses the recorded ranges.
Run after the timed session so oracle work cannot affect its latency.
"""
import argparse
import json
from pathlib import Path
import sys

import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'software'))


def main():
    from moonshine.model import load
    from moonshine.ort_decoder import Runtime, digest
    from moonshine.ort_encoder import Runtime as EncoderRuntime
    from moonshine.streaming import Stream
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('wav', type=Path)
    p.add_argument('events', type=Path)
    p.add_argument('--ort-decoder', type=Path, required=True)
    p.add_argument('--ort-encoder', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if a.out.exists():
        p.error('Use a new output path')
    rows = [json.loads(line) for line in a.events.read_text().splitlines() if line.strip()]
    ready = next(r for r in rows if r['type'] == 'ready')
    if ready['source'] != 'replay':
        p.error('Requires a replay log and its exact source WAV')
    if ready.get('source_audio_sha256') != digest(a.wav):
        raise ValueError('Source WAV differs from the recorded session')
    metadata = ready['model']
    size = metadata['model'].rsplit('-', 1)[-1]
    audio, rate = sf.read(a.wav, dtype='float32')
    if rate != 16000 or audio.ndim != 1:
        p.error('Expected mono 16 kHz source')
    if ready.get('highpass_hz'):
        from moonshine.capture import BLOCK, Highpass
        import numpy as np
        highpass = Highpass(ready['highpass_hz'])
        audio = np.concatenate([highpass.process(audio[i:i+BLOCK]) for i in range(0, len(audio), BLOCK)])
    model, processor, meta = load(size, 'oracle', threads=metadata['threads'])
    if any(meta[k] != metadata[k] for k in ('model', 'revision', 'runtime_sources_sha256')):
        raise ValueError('Source/model changed since the FPGA recording')
    decoder = Runtime(a.ort_decoder, meta, threads=ready['decoder_runtime']['threads'], scheduling='run-spin',
                      precision=ready['decoder_runtime']['precision'])
    encoder = EncoderRuntime(a.ort_encoder, meta, threads=ready['encoder_runtime']['threads'],
                             precision=ready['encoder_runtime']['attention_precision'])
    for actual, expected in ((decoder.metadata, ready['decoder_runtime']), (encoder.metadata, ready['encoder_runtime'])):
        if actual != expected:
            raise ValueError('Native artifacts or runtime differ from the recorded session')
    states = {}
    count = finals = 0
    mismatches = []
    for row in rows:
        if row['type'] not in ('partial', 'final-pending', 'final', 'incomplete'):
            continue
        utterance = row['utterance_id']
        if utterance not in states:
            stream = Stream(model, processor, max_seconds=ready['max_seconds'],
                            decoder_runtime=decoder, encoder_runtime=encoder,
                            preview_seconds=ready.get('preview_seconds'),
                            preview_overlap_seconds=ready.get('preview_overlap_seconds') or 0,
                            defer_final=ready.get('accuracy_mode', False))
            states[utterance] = [stream, row['audio_start_sample'], None]
        state = states[utterance]
        stream, position, pending = state
        end = row['audio_end_sample']
        if not 0 <= position <= end <= len(audio):
            raise ValueError('Invalid recorded source range')
        if row.get('refined'):
            if pending is None or position != end:
                raise ValueError('Refined final has no matching pending phrase')
            event = {**pending.run(), 'frames': sum(x.shape[1] for x in stream.memory)}
        else:
            event = stream.push((audio[position:end]*ready['gain']).clip(-1, 1), final=row['type'] != 'partial')
            if row['type'] == 'final-pending':
                state[2] = stream.final_job
        if event['tokens'] != row['tokens'] or event['frames'] != row['frames']:
            mismatches.append({'event': count, 'expected_tokens': event['tokens'], 'actual_tokens': row['tokens']})
        state[1] = end
        if row['type'] in ('final', 'incomplete'):
            del states[utterance]
        count += 1
        finals += int(row['type'] == 'final')
    result = {'passed': count > 0 and not mismatches and not states,
              'updates': count, 'finals': finals, 'mismatches': mismatches,
              'runtime_sources_sha256': meta['runtime_sources_sha256']}
    with a.out.open('x', encoding='utf-8') as f:
        f.write(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result), flush=True)
    raise SystemExit(0 if result['passed'] else 2)


if __name__ == '__main__':
    main()
