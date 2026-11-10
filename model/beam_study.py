"""Reproducible two-mic simulation; run from the repository root (see --help).

Only generated artifacts under data/beam_study are written. The existing fixed
front end and INT8 golden network are called directly, without approximations.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import pickle
import time

import numpy as np
import soundfile as sf

from eval_wer import distance, records, testset
from frontend import log_normalize
from frontend_fixed import beamform_fixed, mel_power_fixed
from golden_int8 import network, round_away
from quartznet_float import greedy

PREFIX = 512
GAIN = 16384
VERSION = 1


def shift(x, delay):
    """Causal zero-filled delay, retaining the original array length."""
    return np.r_[np.zeros(delay), x[:len(x)-delay]] if delay else x.copy()


def quantize(x):
    q = np.rint(x * 131072).astype(np.int64)
    if np.any((q < -131072) | (q > 131071)):
        raise ValueError('capture clips at signed18 conversion')
    return q


def beam(a, b, delay):
    # Nonzero registers avoid delay=0's read-before-write 256-sample ring lag.
    y = beamform_fixed(a, b, delay1=delay+1, delay2=1, gain=GAIN)
    return y[PREFIX+delay+2:PREFIX+delay+2+len(a)-PREFIX-16]


def energy(x):
    return float(np.dot(x.astype(np.float64), x.astype(np.float64)))


def recognize(x, q):
    start = time.monotonic()
    _, mel = mel_power_fixed(x)
    features, _ = log_normalize(mel, mode='utterance')
    length = features.shape[1]
    features = np.pad(features, ((0, 0), (0, (-length) % 16)))
    inputs = np.clip(round_away(features/q['input_scale']), -128, 127).astype(np.int8)
    hyp = greedy(network(inputs, q['layers'], length=length))
    return hyp, time.monotonic()-start


def source(rows, index, n):
    row = rows[index % len(rows)]
    x, sr = sf.read(row['path'], dtype='float64')
    assert sr == 16000
    x -= x.mean()
    x = np.tile(x, (n+len(x)-1)//len(x))[:n]
    return x / np.sqrt(np.mean(x*x)), row['id']


def noises(kind, n, rows, seed, clip_index):
    rng = np.random.default_rng(seed)
    if kind == 'white':
        return [rng.standard_normal(n), rng.standard_normal(n)], []
    # Eight different dev-clean speakers per microphone; no shared utterances.
    ids = []
    channels = []
    for c in range(1 if kind == 'directional' else 2):
        parts = []
        for j in range(1 if kind == 'directional' else 8):
            x, uid = source(rows, clip_index*17+c*8+j, n)
            parts.append(np.roll(x, int(rng.integers(n))))
            ids.append(uid)
        channels.append(sum(parts)/np.sqrt(len(parts)))
    return channels, ids


def init_worker():
    global QUANT, DEV
    with open('data/int8.pkl', 'rb') as f:
        QUANT = pickle.load(f)
    # Select one utterance per dev speaker before deterministic indexing.
    speakers = {}
    for row in records('dev-clean'):
        speakers.setdefault(row['id'].split('-')[0], row)
    DEV = list(speakers.values())


def evaluate(task):
    index, row, kind, snr, delays, seed, save_audio = task
    speech, sr = sf.read(row['path'], dtype='float64')
    assert sr == 16000
    n = len(speech)
    speech -= speech.mean()
    raw_noise, noise_ids = noises(kind, n+16, DEV, seed+index*1009, index)
    # Identical signal/noise realization across SNRs and target delays.
    noise_rms = np.sqrt(np.mean(speech*speech))*10**(-snr/20) if kind != 'clean' else 0
    raw_noise = [x/np.sqrt(np.mean(x[:n]**2))*noise_rms for x in raw_noise]
    target = np.r_[speech, np.zeros(16)]
    if kind == 'directional':
        noise_pair = [shift(raw_noise[0], 3), raw_noise[0]]
    else:
        noise_pair = raw_noise
    # Shared scale for both microphones and every steering condition in this
    # task; generous headroom also prevents wrap in the DC-filter histories.
    peak = max(np.max(abs(target))+np.max(abs(x)) for x in noise_pair)
    scale = 0.20 / peak
    result = []
    for delay in [None] + delays:
        clean = [target, shift(target, delay or 0)]
        clean = [quantize(np.r_[np.zeros(PREFIX), x*scale]) for x in clean]
        mixed = [quantize(np.r_[np.zeros(PREFIX), (x+y)*scale])
                 for x, y in zip([target, shift(target, delay or 0)], noise_pair)]
        if delay is None:
            signal = clean[0][PREFIX:PREFIX+n]
            capture = mixed[0][PREFIX:PREFIX+n]
            filtered_signal = beam(clean[0], clean[0], 0)
            filtered_capture = beam(mixed[0], mixed[0], 0)
        else:
            signal = beam(*clean, delay)
            capture = beam(*mixed, delay)
            filtered_signal = signal
            filtered_capture = capture
        assert len(capture) == n
        assert np.max(abs(capture)) < 131071
        hyp, seconds = recognize(capture, QUANT)
        record = dict(id=row['id'], kind=kind, snr=snr, delay=delay,
                      hyp=hyp, reference=row['text'], errors=distance(row['text'].split(), hyp.split()),
                      words=len(row['text'].split()), signal_energy=energy(signal),
                      noise_energy=energy(capture-signal), filtered_signal_energy=energy(filtered_signal),
                      filtered_noise_energy=energy(filtered_capture-filtered_signal),
                      noise_ids=noise_ids, scale=scale, seconds=seconds)
        result.append(record)
        if save_audio:
            stem = f'{row["id"]}_{kind}_{snr}_{"single" if delay is None else delay}'
            # PCM_24 stores signed18 exactly after normalization by 2**17.
            sf.write(str(Path(save_audio)/(stem+'.wav')), capture/131072, 16000, subtype='PCM_24')
    return result


def summarize(rows):
    groups = {}
    for row in rows:
        key = (row['kind'], row['snr'], row['delay'])
        g = groups.setdefault(key, dict(kind=key[0], snr=key[1], delay=key[2], clips=0,
                                       errors=0, words=0, signal_energy=0., noise_energy=0.,
                                       filtered_signal_energy=0., filtered_noise_energy=0.))
        g['clips'] += 1
        for name in ('errors', 'words', 'signal_energy', 'noise_energy',
                     'filtered_signal_energy', 'filtered_noise_energy'):
            g[name] += row[name]
    for g in groups.values():
        g['wer'] = g['errors']/g['words']
        for prefix in ('', 'filtered_'):
            s, n = g[prefix+'signal_energy'], g[prefix+'noise_energy']
            g[prefix+'snr_db'] = 10*np.log10(s/n) if n else None
    return list(groups.values())


def self_check():
    impulse = np.zeros(PREFIX+1024+16, dtype=np.int64)
    impulse[PREFIX] = 10000
    for d in range(4):
        a = beam(impulse, shift(impulse, d).astype(np.int64), d)
        b = beam(impulse, impulse, 0)
        np.testing.assert_array_equal(a, b)
        assert a[0] == 10000
    # delay0 reads the old ring entry; do not silently treat it as bypass.
    y = beamform_fixed(impulse, impulse, 0, 0, GAIN)
    assert y[PREFIX+257] == 10000
    rng = np.random.default_rng(9)
    a, b = rng.normal(0, .01, (2, 160000))
    qa, qb = [quantize(np.r_[np.zeros(PREFIX), x, np.zeros(16)]) for x in (a, b)]
    single = beam(qa, qa, 0)
    summed = beam(qa, qb, 0)
    gain = 10*np.log10(energy(single)/energy(summed))
    assert 2.9 < gain < 3.1, gain
    print(f'PASS alignment d=0..3, delay0 ring lag, independent-noise gain {gain:.4f} dB', flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', default='data/beam_study')
    p.add_argument('--workers', type=int, default=4)
    p.add_argument('--delays', type=int, nargs='+', default=[0, 1, 2, 3])
    p.add_argument('--snrs', type=int, nargs='+', default=[20, 10, 5, 0])
    p.add_argument('--kinds', nargs='+', default=['white', 'babble', 'directional'],
                   choices=['white', 'babble', 'directional'])
    p.add_argument('--seed', type=int, default=20261105)
    p.add_argument('--limit', type=int, default=40, help='40 for final results')
    p.add_argument('--save-audio', action='store_true')
    p.add_argument('--self-check', action='store_true')
    args = p.parse_args()
    if args.self_check:
        self_check()
        return
    if any(d < 0 or d > 3 for d in args.delays):
        p.error('target delay must be in 0..3')
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = testset()[:args.limit]
    audio = out/'audio' if args.save_audio else None
    if audio:
        audio.mkdir(exist_ok=True)
    config = dict(version=VERSION, seed=args.seed, delays=args.delays, snrs=args.snrs,
                  kinds=args.kinds, ids=[r['id'] for r in rows], normalization='utterance',
                  hashes={str(path): hashlib.sha256(Path(path).read_bytes()).hexdigest()
                          for path in ['data/int8.pkl', 'model/frontend_fixed.py',
                                       'model/frontend.py', 'model/golden_int8.py',
                                       'model/testset_40.txt', __file__]})
    config_path = out/'config.json'
    if config_path.exists() and json.loads(config_path.read_text()) != config:
        raise ValueError('configuration changed: use a new --out directory')
    config_path.write_text(json.dumps(config, indent=2)+'\n')
    checkpoint = out/'clips.jsonl'
    done = [json.loads(line) for line in checkpoint.read_text().splitlines()] if checkpoint.exists() else []
    completed = {(r['id'], r['kind'], r['snr']) for r in done}
    tasks = [(i, r, kind, snr, args.delays, args.seed, str(audio) if audio else None)
             for i, r in enumerate(rows)
             for kind, snr in [('clean', 100)] + [(k, s) for k in args.kinds for s in args.snrs]
             if (r['id'], kind, snr) not in completed]
    with concurrent.futures.ProcessPoolExecutor(args.workers, initializer=init_worker) as pool:
        futures = [pool.submit(evaluate, task) for task in tasks]
        with checkpoint.open('a') as f:
            for future in concurrent.futures.as_completed(futures):
                batch = future.result()
                for r in batch:
                    f.write(json.dumps(r)+'\n')
                f.flush()
                os.fsync(f.fileno())
                done.extend(batch)
                print(f'{len(done)} captures: {batch[0]["id"]} {batch[0]["kind"]} '
                      f'{batch[0]["snr"]} dB; {sum(r["seconds"] for r in batch):.1f} worker s', flush=True)
    summary = dict(config=config, conditions=summarize(done), complete=True)
    (out/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary['conditions'], indent=2))


if __name__ == '__main__':
    main()
