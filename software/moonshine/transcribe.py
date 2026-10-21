"""Stream 16 kHz mono audio from a WAV or PCM16 stdin; emit revisable JSON updates."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

import numpy as np

from .matrix import BulkDevice
from .model import load
from .streaming import Stream


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('wav', help='16 kHz mono WAV, or - for signed little-endian PCM16 stdin')
    p.add_argument('--size', choices=('small', 'tiny'), default='small')
    p.add_argument('--backend', choices=('cpu', 'cpu-int8', 'oracle', 'fpga'), default='cpu')
    p.add_argument('--scope', choices=('frontend-ffn', 'encoder-ffn', 'encoder', 'all'), default='encoder-ffn')
    p.add_argument('--library', default='build/native/libqnc_moonshine.so')
    p.add_argument('--chunk-ms', type=int, default=500)
    p.add_argument('--threads', type=int, default=2)
    p.add_argument('--runs', type=int, default=1)
    p.add_argument('--warmup', type=int, default=1, help='Untimed WAV passes before measured runs')
    p.add_argument('--max-seconds', type=float, default=30)
    p.add_argument('--max-tokens', type=int, default=256)
    p.add_argument('--float-cpu-tail', action='store_true', help='Use float32 for non-offloaded projections in oracle/FPGA modes')
    p.add_argument('--no-draft', action='store_true', help='Regenerate each partial transcript without hypothesis verification')
    p.add_argument('--ort-decoder', type=Path, help='Directory exported by moonshine.ort_decoder')
    p.add_argument('--decoder-precision', choices=('float', 'int8'), default='int8')
    p.add_argument('--decoder-threads', type=int, default=2)
    p.add_argument('--decoder-scheduling', choices=('sleep', 'run-spin'), default='sleep')
    p.add_argument('--ort-encoder', type=Path, help='Directory exported by moonshine.ort_encoder')
    p.add_argument('--encoder-precision', choices=('float', 'int8'), default='int8')
    p.add_argument('--encoder-threads', type=int, default=4)
    p.add_argument('--encoder-scheduling', choices=('sleep', 'run-spin'), default='sleep')
    p.add_argument('--out', type=Path)
    p.add_argument('--final-only', action='store_true', help='Encode each chunk; decode once at utterance end')
    a = p.parse_args()
    if min(a.chunk_ms, a.threads, a.runs, a.decoder_threads, a.encoder_threads) < 1 or a.max_seconds <= 0 or a.max_tokens < 1 or a.warmup < 0:
        p.error('Chunk, thread, run and utterance limits must be positive')
    if a.ort_decoder and a.scope == 'all':
        p.error('ORT decoder cannot be combined with decoder projections offloaded using --scope all')
    if a.ort_encoder and a.scope != 'encoder-ffn':
        p.error('ORT encoder requires --scope encoder-ffn')
    chunk = a.chunk_ms * 16
    audio = None
    if a.wav != '-':
        import soundfile as sf
        audio, rate = sf.read(a.wav, dtype='float32')
        if rate != 16000 or audio.ndim != 1 or len(audio) == 0 or not np.isfinite(audio).all():
            p.error('Supply nonempty, finite, mono 16 kHz audio')
        if len(audio) > a.max_seconds * 16000:
            p.error('WAV exceeds utterance limit')
    elif a.runs != 1:
        p.error('Live stdin supports one run')
    if audio is None:
        a.warmup = 0
    start = time.perf_counter()
    device = BulkDevice(a.library) if a.backend == 'fpga' else None
    try:
        model, processor, metadata = load(a.size, a.backend, device, a.scope, a.threads, not a.float_cpu_tail)
        decoder_runtime = None
        if a.ort_decoder:
            from .ort_decoder import Runtime
            decoder_runtime = Runtime(a.ort_decoder, metadata, a.decoder_threads, a.decoder_precision, a.decoder_scheduling)
        metadata['decoder_runtime'] = decoder_runtime.metadata if decoder_runtime else {'engine': 'torch'}
        encoder_runtime = None
        if a.ort_encoder:
            from .ort_encoder import Runtime as EncoderRuntime
            encoder_runtime = EncoderRuntime(a.ort_encoder, metadata, a.encoder_threads, a.encoder_precision, a.encoder_scheduling)
        metadata['encoder_runtime'] = encoder_runtime.metadata if encoder_runtime else {'engine': 'torch'}
        init = time.perf_counter() - start
        records = []
        first_call_seconds = None
        for run in range(-a.warmup, a.runs):
            stream = Stream(model, processor, a.max_seconds, a.max_tokens, not a.no_draft, decoder_runtime, encoder_runtime)
            events = []
            audio_hash = hashlib.sha256()
            def push(samples, final=False):
                audio_hash.update(np.asarray(samples, dtype='<f4').tobytes())
                t = time.perf_counter()
                result = stream.push(samples, final=final, decode=final or not a.final_only)
                result.update(run=run, audio_seconds=stream.samples / 16000,
                              processing_seconds=time.perf_counter()-t)
                events.append(result)
                print(json.dumps(result), flush=True)
            if audio is not None:
                for offset in range(0, len(audio), chunk):
                    push(audio[offset:offset+chunk])
            else:
                # Pipe from arecord: arecord -q -t raw -f S16_LE -r 16000 -c 1
                while True:
                    data = sys.stdin.buffer.read(chunk * 2)
                    if not data:
                        break
                    if len(data) % 2:
                        raise ValueError('Incomplete PCM16 sample at end of stdin')
                    push(np.frombuffer(data, dtype='<i2').astype(np.float32) / 32768)
            if not stream.samples:
                raise ValueError('No audio received')
            push(np.empty(0, dtype=np.float32), final=True)
            elapsed = sum(e['processing_seconds'] for e in events)
            if first_call_seconds is None:
                first_call_seconds = elapsed
            if run < 0:
                continue
            records.append({'events': events, 'total_processing_seconds': elapsed,
                            'final_processing_seconds': events[-1]['processing_seconds'],
                            'profile': stream.profile,
                            'decode_stats': stream.decode_stats,
                            'text': events[-1]['text'], 'tokens': events[-1]['tokens']})
        totals = [r['total_processing_seconds'] for r in records]
        report = {**metadata, 'initialization_seconds': init, 'first_call_seconds': first_call_seconds,
                  'warmup_runs': a.warmup, 'chunk_ms': a.chunk_ms,
                  'draft_reuse': not a.no_draft,
                  'final_only': a.final_only, 'size': a.size, 'sample_count': stream.samples,
                  'platform': platform.platform(), 'runs': records, 'max_tokens': a.max_tokens,
                  'p50_seconds': float(np.median(totals)), 'p95_seconds': float(np.percentile(totals, 95)),
                  'realtime_factor': float(np.median(totals)) / (stream.samples / 16000),
                  'physical': device.stats() if device is not None else None}
        report['audio_sha256'] = audio_hash.hexdigest()
        import torch, transformers
        report['torch_version'] = torch.__version__
        report['transformers_version'] = transformers.__version__
        if a.out:
            a.out.parent.mkdir(parents=True, exist_ok=True)
            a.out.write_text(json.dumps(report, indent=2)+'\n')
    finally:
        if device is not None:
            device.close()


if __name__ == '__main__':
    main()
