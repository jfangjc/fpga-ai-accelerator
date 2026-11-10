"""Summarise collected timelines, VAD calibration and CPU reference agreement."""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tests/t1'))
from bench_app import compare, percentile, score

folder = Path(sys.argv[1])
rows = []
totals = dict(words=0, final_errors=0, preview_errors=0)
updates, computes, vad = [], [], []
preview_compute, final_compute = [], []
merged_total, lost_total = 0, 0
for path in sorted(folder.glob('*.jsonl')):
    clip = path.stem
    speaker, chapter, _ = clip.split('-')
    transcript = ROOT/f'data/LibriSpeech/test-clean/{speaker}/{chapter}/{speaker}-{chapter}.trans.txt'
    if not transcript.exists():
        transcript = next((ROOT/'data/LibriSpeech').rglob(f'{speaker}-{chapter}.trans.txt'))
    ref = next(line.split(' ',1)[1] for line in transcript.read_text().splitlines() if line.startswith(clip+' '))
    events = [e for line in path.read_text().splitlines()
              if not (e := json.loads(line)).get('event_only')]
    s = score(events,ref)
    for key in totals: totals[key] += s[key]
    rows.append(dict(id=clip,**s, final=' '.join(e['final'] for e in sorted(events,key=lambda e:e['phrase']) if e['state']=='Committed')))
    updates += [e['update_ms'] for e in events if e['state']=='Speaking'
                and not e.get('bulk') and e.get('latency_valid')]
    computes += [e['compute_ms'] for e in events]
    preview_compute += [e['compute_ms'] for e in events if e['state']=='Speaking']
    final_compute += [e['compute_ms'] for e in sorted(events,key=lambda e:e['phrase']) if e['state']=='Committed']
    merged_total += max(e['merged'] for e in events)
    lost_total += max(e['lost_frames'] for e in events)
    csvpath = path.with_suffix('.vad.csv')
    if csvpath.exists():
        vad += [(float(e),int(active)) for _,e,active in csv.reader(csvpath.open())]
result = dict(clips=len(rows),**totals,
              final_wer=100*totals['final_errors']/totals['words'],
              preview_wer=100*totals['preview_errors']/totals['words'],
              update_ms_rendered={k:percentile(updates,q) for k,q in [('p50',.5),('p95',.95),('max',1)]},
              compute_ms={k:percentile(computes,q) for k,q in [('p50',.5),('p95',.95)]}, rows=rows)
result.update(preview_updates=len(preview_compute), finals=len(final_compute),
              merged=merged_total, lost_frames=lost_total,
              preview_compute_ms={k:percentile(preview_compute,q) for k,q in [('p50',.5),('p95',.95)]},
              final_compute_ms={k:percentile(final_compute,q) for k,q in [('p50',.5),('p95',.95)]})
if vad:
    ordered = sorted(vad)
    wrong = sum(not active for _,active in vad)
    best = (wrong, 0.)
    for i,(energy,active) in enumerate(ordered):
        wrong += 1 if active else -1
        if i+1<len(ordered) and ordered[i+1][0]>energy:
            best = min(best,(wrong,(energy+ordered[i+1][0])/2))
    result['vad'] = dict(frames=len(vad),default_threshold=.01,
                        default_disagreement=sum((e>=.01)!=bool(a) for e,a in vad),
                        fitted_threshold=best[1],fitted_disagreement=best[0],
                        caveat='In-sample fit, not a held-out validation; continuous PL frame lattice')
cpu = ROOT/'results/app/measurements-cpu.jsonl'
if cpu.exists():
    result['cpu_reference'] = compare([json.loads(s) for s in cpu.read_text().splitlines()],
        json.loads((ROOT/'data/export/stream_ref/1089-134691-0023.json').read_text()))
print(json.dumps(result,indent=2))
