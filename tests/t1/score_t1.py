"""Score the locked board batch with tests/t1/bench_app.py and absolute main data.

Usage (WSL): python tests/t1/score_t1.py MAIN_CHECKOUT OUTPUT_DIRECTORY
"""
import csv
import importlib.util
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('bench_app', root/'tests/t1/bench_app.py')
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)
main, out = map(Path, sys.argv[1:])
bench.ROOT = main
sys.argv = ['bench_app.py', '--ab', '--power', '--source', 'pl-inject',
            '--clips', *(root/'model/testset_40.txt').read_text().split(),
            '--threads', '3', '--score-only', '--output', str(out)]
bench.main()
summary = json.loads((out/'summary.json').read_text())
assert len(summary['pairs']) == 40
assert summary['cpu']['clips'] == summary['pl']['clips'] == 40
rows = []
extra = {}
for backend in ['cpu', 'pl']:
    powers, loads, service, lost = [], [], [], []
    for pair in summary['pairs']:
        clip = pair['clip']
        tag = f'{clip}.{backend}'
        events = [json.loads(s) for s in (out/f'{tag}.jsonl').read_text().splitlines()]
        ev = [e for e in events if not e.get('event_only')]
        assert ev and all(e.get('latency_valid') for e in ev), tag
        assert all(e.get('capture_anchor') == 'fifo-first-fft' for e in ev), tag
        assert all(e.get('display') == 'fb' for e in ev), tag
        powers.append(bench.power_value(out/f'{tag}.power'))
        # pidstat -h: Time UID PID %usr %system %guest %wait %CPU CPU Command.
        clip_load = []
        for line in (out/f'{tag}.pidstat').read_text().splitlines():
            fields = line.split()
            if len(fields) >= 10 and fields[-1] == 'sttcap':
                clip_load.append(float(fields[-3]))
        loads.extend(clip_load)
        samples = []
        with (out/f'{tag}.stages.csv').open() as f:
            for s in csv.DictReader(f):
                if s['state'] == 'S' and float(s['inference']) > 0 and float(s['fb']) > 0:
                    samples.append((float(s['fb'])-float(s['inference']))*1000)
        speaking = [e for e in ev if e['state'] == 'Speaking']
        assert all(not e.get('bulk') for e in ev), tag
        if backend == 'pl':
            assert all(e.get('backend') == 'pl-network' and e.get('commands') == 1
                       for e in speaking), tag
        # End-to-end service: backend compute through completed framebuffer write.
        assert len(samples) == len(speaking), tag
        service.extend(e['compute_ms']+s for e, s in zip(speaking, samples))
        lost.append(max(e.get('lost_frames', 0) for e in ev))
        clip_stats = {}
        for key, values in [('update_ms', [e['update_ms'] for e in speaking]),
                            ('compute_ms', [e['compute_ms'] for e in speaking])]:
            for quantile, q in [('p50', .5), ('p95', .95), ('max', 1)]:
                clip_stats[f'{key}_{quantile}'] = bench.percentile(values, q)
        rows.append(dict(clip=clip, backend=backend, order=' '.join(pair['order']),
                         **clip_stats,
                         **pair[backend], power_max_w=powers[-1]['max_w'],
                         cpu_mean_pct=sum(clip_load)/len(clip_load) if clip_load else None,
                         merged=max(e['merged'] for e in ev), lost_frames=lost[-1]))
    extra[backend] = dict(
        power_mean_w=sum(p['mean_w']*p['samples'] for p in powers)/sum(p['samples'] for p in powers),
        power_max_w=max(p['max_w'] for p in powers),
        ps_process_cpu_mean_pct=sum(loads)/len(loads) if loads else None,
        ps_process_cpu_max_pct=max(loads) if loads else None,
        service_ms={k: bench.percentile(service, q) for k, q in [('p50', .5), ('p95', .95), ('max', 1)]},
        lost_frames=sum(lost))
with (out/'per-clip.csv').open('w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator='\n')
    w.writeheader()
    w.writerows(rows)
(out/'extra-summary.json').write_text(json.dumps(extra, indent=2)+'\n')
print(json.dumps(extra, indent=2))
