"""Reference-based WER evaluation for live logs and precision comparisons.

Use human transcripts of representative general conversation. No model output
is treated as ground truth. Timing here is offline, not live-caption latency.
"""
import argparse
import json
from pathlib import Path
import time

import numpy as np

from .transcript import words


def word_errors(reference, hypothesis):
    a, b = [w[0] for w in words(reference)], [w[0] for w in words(hypothesis)]
    # Cell = (distance, substitutions, deletions, insertions).
    previous = [(i, 0, 0, i) for i in range(len(b)+1)]
    for i, x in enumerate(a, 1):
        current = [(i, 0, i, 0)]
        for j, y in enumerate(b, 1):
            if x == y:
                current.append(previous[j-1])
            else:
                d, s, delete, insert = previous[j-1]
                sub = (d+1, s+1, delete, insert)
                d, s, delete, insert = previous[j]
                deletion = (d+1, s, delete+1, insert)
                d, s, delete, insert = current[j-1]
                insertion = (d+1, s, delete, insert+1)
                current.append(min((sub, deletion, insertion), key=lambda cell: cell[0]))
        previous = current
    distance, substitutions, deletions, insertions = previous[-1]
    return {'reference_words': len(a), 'substitutions': substitutions, 'deletions': deletions,
            'insertions': insertions, 'errors': distance, 'wer': distance/len(a) if a else None}


def signal_report(audio):
    centered = audio-np.mean(audio, dtype=np.float64)
    rms = float(np.sqrt(np.mean(centered*centered, dtype=np.float64)))
    return {'seconds': len(audio)/16000, 'dc_offset': float(np.mean(audio)),
            'ac_rms_dbfs': float(20*np.log10(max(rms, 1e-12))),
            'peak': float(np.max(np.abs(audio))),
            'clipped_fraction': float(np.mean(np.abs(audio) >= .999))}


def main():
    import soundfile as sf
    from .ort_decoder import digest
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest', type=Path, help='JSON: {"clips": [{"wav": "clip.wav", "text": "human transcript"}]}')
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--variant', choices=('float', 'fpga-int8', 'fpga-float-attention', 'fpga-float-decoder'))
    mode.add_argument('--live-events', type=Path, help='Score one paced replay log against its human transcript')
    p.add_argument('--size', choices=('tiny', 'small'), default='tiny')
    p.add_argument('--ort-decoder', type=Path)
    p.add_argument('--ort-encoder', type=Path)
    p.add_argument('--library', default='build/native/libqnc_moonshine.so')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if a.out.exists():
        p.error('Use a new output file')
    dataset = json.loads(a.manifest.read_text())
    clips = dataset['clips']
    if not clips or any(not isinstance(c.get('text'), str) or not words(c['text']) for c in clips):
        p.error('Nonempty human transcripts are required')
    if a.live_events and len(clips) != 1:
        p.error('One source clip is required when scoring a live session')
    if a.variant and a.variant != 'float' and (not a.ort_decoder or not a.ort_encoder):
        p.error('FPGA variants require matching native encoder and decoder artifacts')
    # Validate every input before loading a model or touching FPGA hardware.
    paths = [(a.manifest.parent/c['wav']).resolve() for c in clips]
    for path in paths:
        info = sf.info(path)
        if info.samplerate != 16000 or info.channels != 1 or info.frames == 0:
            p.error('Expected nonempty mono 16 kHz audio: '+str(path))
        if not a.live_events and info.frames > 30*16000:
            p.error('Offline clips must fit the 30-second model context')
    device = None
    metadata = {}
    results = []
    try:
        if a.variant:
            from .matrix import BulkDevice
            from .model import load
            from .ort_decoder import Runtime
            from .ort_encoder import Runtime as EncoderRuntime
            from .streaming import Stream
            if a.variant != 'float':
                device = BulkDevice(a.library)
            model, processor, metadata = load(a.size, 'fpga' if device else 'cpu', device=device, threads=2)
            decoder = Runtime(a.ort_decoder, metadata, threads=4, scheduling='run-spin',
                precision='float' if a.variant == 'fpga-float-decoder' else 'int8') if device else None
            encoder = EncoderRuntime(a.ort_encoder, metadata, threads=4,
                precision='float' if a.variant == 'fpga-float-attention' else 'int8') if device else None
            metadata = {**metadata, 'decoder_runtime': decoder.metadata if decoder else None,
                        'encoder_runtime': encoder.metadata if encoder else None}
        for clip, path in zip(clips, paths):
            audio, _ = sf.read(path, dtype='float32')
            if not np.isfinite(audio).all():
                raise ValueError('Nonfinite source audio')
            if a.live_events:
                rows = [json.loads(line) for line in a.live_events.read_text().splitlines() if line.strip()]
                ready = next(r for r in rows if r['type'] == 'ready')
                summary = next((r for r in reversed(rows) if r['type'] == 'summary'), {})
                if ready.get('source_audio_sha256') != digest(path) or ready['source'] != 'replay':
                    raise ValueError('Live source WAV does not match the reference clip')
                if summary.get('exit_code') != 0 or summary.get('incomplete') or summary.get('pending_refinements'):
                    raise ValueError('Cannot score an incomplete live session as a completed transcript')
                finals = [r for r in rows if r['type'] == 'final']
                text = ' '.join(r['text'] for r in finals)
                seconds = sum(r.get('processing_seconds', 0) for r in rows
                              if r['type'] in ('partial', 'final'))
                limited = any(r.get('token_limit_reached') for r in finals)
                metadata = ready
            else:
                stream = Stream(model, processor, max_seconds=30, decoder_runtime=decoder, encoder_runtime=encoder)
                start = time.perf_counter()
                event = stream.push(audio, final=True)
                seconds, text, limited = time.perf_counter()-start, event['text'], event['token_limit_reached']
            results.append({'wav': clip['wav'], 'wav_sha256': digest(path), 'reference': clip['text'], 'hypothesis': text,
                'score': word_errors(clip['text'], text), 'signal': signal_report(audio),
                'processing_seconds': seconds, 'token_limit_reached': limited})
        totals = {k: sum(r['score'][k] for r in results)
                  for k in ('reference_words', 'errors', 'substitutions', 'deletions', 'insertions')}
        totals['wer'] = totals['errors']/totals['reference_words']
        report = {'variant': a.variant or 'live-session', 'manifest_sha256': digest(a.manifest),
            'dataset': dataset.get('dataset', 'User-supplied reference transcripts'),
            'model': metadata, 'clips': results, 'total': totals,
            'physical': device.stats() if device else None,
            'complete': not any(r['token_limit_reached'] for r in results),
            'normalization': 'Unicode NFKC, casefold, punctuation stripped; numbers are not expanded',
            'timing_scope': 'Offline accuracy comparison; not a live latency benchmark'}
        with a.out.open('x', encoding='utf-8') as f:
            f.write(json.dumps(report, indent=2)+'\n')
        print(json.dumps({'variant': report['variant'], 'total': totals, 'complete': report['complete']}))
        raise SystemExit(0 if report['complete'] else 2)
    finally:
        if device:
            device.close()


if __name__ == '__main__':
    main()
