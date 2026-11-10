"""Windows PC collector/scorer. Requires Python, soundfile, NumPy and PowerShell.

Board assets default to the accepted CPU benchmark's ~/stt/cpu directory.
Use --prepare to convert/copy the 40 LibriSpeech clips first.
--backend pl runs the PL engine (sttcap --backend pl --program QNP2).
--ab is the paired T1 mode: each clip runs CPU and PL back to back (order
alternating per clip); summary.json holds both backends plus per-clip pairs.
--power samples board input power with tools/power.sh (installed as
~/stt/power.sh) in parallel with each run, over 90% of the paced replay, plus
an idle baseline. Per backend: per-update compute_ms, capture-clock-anchored
renderer-time update_ms (L_update), final and committed-preview WER. Current
PL runs export hardware-clock-fit readiness/rates; startup retains the FIFO
probe at nominal rate until ten hardware-counter bins are available.
Each run also records sttcap --stages (per-event output stage times); the
summary's output_stages_ms is the per-stage p50/p95/max (tests/app/stall_check.py).
PPM evidence is off in benchmark runs (--no-ppm) unless --ppm is given.
Every board execution holds ~/stt/board.lock; no credentials enter this script.
"""
import argparse
import json
import shlex
import csv
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tests/app'))
from stall_check import durations  # noqa: E402


def board(action, source, destination=None):
    cmd = ['powershell.exe', '-NoProfile', '-File', str(ROOT/'tools/board.ps1'),
           '-Action', action, '-Source', source]
    if destination:
        cmd += ['-Destination', str(destination)]
    if action == 'run':
        cmd += ['-Sudo']
    subprocess.run(cmd, check=True)


def distance(a, b):
    row = list(range(len(b)+1))
    for i, x in enumerate(a, 1):
        new = [i]
        for j, y in enumerate(b, 1):
            new.append(min(new[-1]+1, row[j]+1, row[j-1]+(x != y)))
        row = new
    return row[-1]


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    position = (len(values)-1)*q
    lo = int(position)
    return values[lo] + (values[min(lo+1, len(values)-1)]-values[lo])*(position-lo)


def score(events, reference):
    finals, previews = {}, {}
    for e in events:
        if e.get('event_only'):
            continue
        if e['state'] == 'Committed':
            finals[e['phrase']] = e['final']
        elif e['state'] == 'Speaking':
            previews[e['phrase']] = e['preview']
    ref = reference.lower().split()
    return {'words': len(ref),
            'final_errors': distance(ref, ' '.join(finals[k] for k in sorted(finals)).split()),
            'preview_errors': distance(ref, ' '.join(previews[k] for k in sorted(previews)).split())}


def compare(events, reference):
    actual = [e for e in events if e['state']=='Speaking' and 'feature_count' in e]
    expected = [e for e in reference['events'] if e['state']=='Speaking' and 'feature_count' in e]
    fields = ('phrase', 'feature_count', 'window_start', 'emitted', 'preview', 'tentative')
    return {'actual_updates': len(actual), 'reference_updates': len(expected),
            'different_updates': sum(any(a.get(k)!=b.get(k) for k in fields)
                                     for a, b in zip(actual, expected)) + abs(len(actual)-len(expected))}


def power_value(path):
    """Parse power.sh output 'samples=N mean_W=X max_W=Y' (None if absent)."""
    if not path.exists():
        return None
    fields = dict(x.split('=', 1) for x in path.read_text().split() if '=' in x)
    if 'mean_W' not in fields:
        return None
    return dict(samples=int(fields['samples']), mean_w=float(fields['mean_W']),
                max_w=float(fields['max_W']))


def stage_samples(path):
    """Per-stage output durations (ms) of one run's --stages CSV, if any."""
    samples = {}
    if path.exists():
        with path.open() as f:
            reader = csv.DictReader(f)
            for row in reader:
                d = durations(row, reader.fieldnames)
                if row['state'] == 'L':
                    d.pop('queue', None)
                for k, v in d.items():
                    samples.setdefault(k, []).append(v)
    return samples


def summarize(runs, backend, args):
    """Aggregate the per-clip runs of one backend."""
    totals = {'words': 0, 'final_errors': 0, 'preview_errors': 0}
    updates, compute, final_compute, power = [], [], [], []
    merges = invalid = 0
    paths, stages, anchors = {}, {}, {}
    fitted_rates = []
    for r in runs:
        for key in totals:
            totals[key] += r['scores'][key]
        events = r['events']
        merges += max((e.get('merged', 0) for e in events), default=0)
        for e in events:
            if e.get('event_only'):
                continue
            if e['state'] == 'Speaking':
                compute.append(e['compute_ms'])
                if e.get('latency_valid') and not e.get('bulk'):
                    updates.append(e['update_ms'])
                elif not e.get('bulk'):
                    invalid += 1
            elif e['state'] == 'Committed':
                final_compute.append(e['compute_ms'])
            if 'backend' in e:
                paths[e['backend']] = paths.get(e['backend'], 0)+1
            anchor=e.get('capture_anchor','unknown')
            anchors[anchor]=anchors.get(anchor,0)+1
            if e['state']=='Speaking' and e.get('clock_fitted'):
                fitted_rates.append(e['sample_rate'])
        if r['power']:
            power.append(r['power']['mean_w'])
        for k, v in r['stages'].items():
            stages.setdefault(k, []).extend(v)
    q3 = [('p50', .5), ('p95', .95), ('max', 1)]
    return dict(totals, backend=backend, source=args.source, clips=len(runs),
                threads=args.threads,
                final_wer=100*totals['final_errors']/max(totals['words'], 1),
                preview_wer=100*totals['preview_errors']/max(totals['words'], 1),
                update_ms={k: percentile(updates, q) for k, q in q3},
                compute_ms={k: percentile(compute, q) for k, q in q3},
                final_compute_ms={k: percentile(final_compute, q) for k, q in q3},
                previews=len(compute), finals=len(final_compute), engine_paths=paths,
                capture_anchors=anchors, clock_fitted_previews=len(fitted_rates),
                fitted_sample_rate_hz={k:percentile(fitted_rates,q) for k,q in q3} if fitted_rates else None,
                power_w=dict(clips=len(power), mean=sum(power)/len(power)) if power else None,
                word_latency='not measured', merged=merges,
                output_stages_ms={k: dict(n=len(v), **{n: percentile(v, q) for n, q in q3})
                                  for k, v in sorted(stages.items())},
                valid_preview_latencies=len(updates), invalid_preview_latencies=invalid)


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--source', choices=['cpu-wav', 'pl-inject'], default='pl-inject')
    p.add_argument('--backend', choices=['cpu', 'pl'], default='cpu')
    p.add_argument('--ab', action='store_true',
                   help='paired A/B: each clip runs CPU and PL, order alternating')
    p.add_argument('--power', action='store_true',
                   help='sample board input power (power.sh) in parallel with each run')
    p.add_argument('--program', default='/home/ubuntu/stt/i2/program.qnp2',
                   help='board QNP2 program for --backend pl')
    p.add_argument('--board-dir', default='/home/ubuntu/stt/app')
    p.add_argument('--sttcap', default='../build/sw/app/sttcap', help='board sttcap path')
    p.add_argument('--clips', nargs='*', help='clip ids (default model/testset_40.txt)')
    p.add_argument('--bulk', action='store_true')
    p.add_argument('--ppm', action='store_true', help='keep caption.ppm evidence (default --no-ppm)')
    p.add_argument('--prepare', action='store_true')
    p.add_argument('--score-only', action='store_true')
    p.add_argument('--limit', type=int, default=40)
    p.add_argument('--threads', type=int, default=3)
    p.add_argument('--mel-threshold', type=float, default=.01)
    p.add_argument('--output', type=Path, default=ROOT/'build/sw/app/measurements')
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    ids = (args.clips or (ROOT/'model/testset_40.txt').read_text().split())[:args.limit]
    backends = ['cpu', 'pl'] if args.ab else [args.backend]
    cpu_dir = '/home/ubuntu/stt/cpu'
    runs = {b: [] for b in backends}
    comparisons, pairs = {}, []
    if args.power and not args.score_only:
        board('run', f'mkdir -p {args.board_dir} && flock /home/ubuntu/stt/board.lock '
                     f'sh /home/ubuntu/stt/power.sh 50 0.1 > {args.board_dir}/power-idle.txt')
        board('get', f'{args.board_dir}/power-idle.txt', args.output/'power-idle.txt')
    idle = power_value(args.output/'power-idle.txt') if args.power else None
    for index, clip in enumerate(ids):
        speaker, chapter, _ = clip.split('-')
        folder = ROOT/'data/LibriSpeech/test-clean'/speaker/chapter
        if not (folder/f'{clip}.flac').exists():
            folder = next((ROOT/'data/LibriSpeech').rglob(f'{clip}.flac')).parent
        transcript = folder/f'{speaker}-{chapter}.trans.txt'
        reference = next(line.split(' ', 1)[1] for line in transcript.read_text().splitlines()
                         if line.startswith(clip+' '))
        if args.prepare:
            import soundfile as sf
            audio, rate = sf.read(folder/f'{clip}.flac')
            if rate != 16000:
                raise ValueError('Expected 16 kHz')
            wav = args.output/f'{clip}.wav'
            sf.write(wav, audio, rate, subtype='PCM_16')
            board('put', str(wav), f'stt/cpu/clips/{clip}.wav')
        # Alternate the A/B order per clip so slow drift favours neither.
        order = backends if index % 2 == 0 else backends[::-1]
        clip_runs = {}
        for backend in order:
            # Legacy CPU-only runs keep the untagged <clip>.jsonl name.
            tag = f'{clip}.{backend}' if args.ab or backend == 'pl' else clip
            path = args.output/f'{tag}.jsonl'
            if not args.score_only:
                sttcap = (f'{args.sttcap} --source {args.source} {cpu_dir}/clips/{clip}.wav '
                          f'--blob {cpu_dir}/quartznet_int8.bin '
                          f'--prior {cpu_dir}/running_prior.json '
                          f'--threads {args.threads} --mel-threshold {args.mel_threshold} '
                          f'--backend {backend} '
                          + (f'--program {args.program} ' if backend == 'pl' else '')
                          + f'--log {tag}.jsonl --stages {tag}.stages.csv '
                          + ('' if args.ppm else '--no-ppm ') + ('--bulk ' if args.bulk else '')
                          + f'> {tag}.stdout 2> {tag}.stderr')
                if args.power:
                    # power.sh prints only after its last sample: sample 90%
                    # of the paced replay (PCM16 mono WAV size -> seconds,
                    # nine 0.1 s samples per second) so the window ends
                    # inside the run.
                    wav = f'{cpu_dir}/clips/{clip}.wav'
                    sttcap = (f'n=$(( ($(stat -c %s {wav}) - 44) * 9 / 32000 )); '
                              f'sh /home/ubuntu/stt/power.sh $n 0.1 > {tag}.power 2>&1 & '
                              f'p=$!; {sttcap}; rc=$?; wait $p; exit $rc')
                command = f'mkdir -p {args.board_dir} && cd {args.board_dir} && {sttcap}'
                board('run', 'flock /home/ubuntu/stt/board.lock sh -c '+shlex.quote(command))
                board('get', f'{args.board_dir}/{tag}.jsonl', path)
                board('get', f'{args.board_dir}/{tag}.stages.csv', args.output/f'{tag}.stages.csv')
                if args.power:
                    board('get', f'{args.board_dir}/{tag}.power', args.output/f'{tag}.power')
            events = [json.loads(line) for line in path.read_text().splitlines()]
            r = dict(clip=clip, events=events, scores=score(events, reference),
                     power=power_value(args.output/f'{tag}.power'),
                     stages=stage_samples(args.output/f'{tag}.stages.csv'))
            runs[backend].append(r)
            clip_runs[backend] = r
            refpath = ROOT/f'data/export/stream_ref/{clip}.json'
            if refpath.exists() and args.bulk:
                comparisons[tag] = compare(events, json.loads(refpath.read_text()))
        if args.ab:
            finals = {b: ' '.join(e['final'] for e in sorted(clip_runs[b]['events'],key=lambda e:e['phrase'])
                                  if e['state'] == 'Committed' and not e.get('event_only'))
                      for b in backends}
            pair = dict(clip=clip, order=order, finals_equal=finals['cpu'] == finals['pl'])
            for b in backends:
                s, ev = clip_runs[b]['scores'], clip_runs[b]['events']
                speaking = [e for e in ev if e['state'] == 'Speaking' and not e.get('event_only')]
                pair[b] = dict(final_errors=s['final_errors'], preview_errors=s['preview_errors'],
                               power_w=(clip_runs[b]['power'] or {}).get('mean_w'),
                               updates=len(speaking),
                               compute_p50=percentile([e['compute_ms'] for e in speaking], .5),
                               update_p50=percentile([e['update_ms'] for e in speaking
                                                      if e.get('latency_valid')], .5))
            pairs.append(pair)
            print(json.dumps(pair), flush=True)
    result = {b: summarize(runs[b], b, args) for b in backends}
    if args.ab:
        result.update(pairs=pairs, finals_equal=sum(p['finals_equal'] for p in pairs),
                      reference_comparisons=comparisons)
        if idle:
            result['power_idle_w'] = idle
        out = result
    else:
        out = dict(result[backends[0]], reference_comparisons=comparisons)
        if idle:
            out['power_idle_w'] = idle
    (args.output/'summary.json').write_text(json.dumps(out, indent=2)+'\n', newline='\n')
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
