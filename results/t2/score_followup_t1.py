"""Score the new PL batch and carry the preceding soak's measured rate into
short clips' startup anchors. Raw JSONL stays unchanged; report both metrics.
"""
import csv
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('bench',ROOT/'tools/bench_app.py')
bench=importlib.util.module_from_spec(spec); spec.loader.exec_module(bench)
folder,soak,baseline=map(Path,sys.argv[1:4])
stderr=soak/'soak.stderr'
if not stderr.exists():
    stderr=soak/'capture.txt'
rates=[float(x) for x in re.findall(r'capture_clock .*sample_rate=([\d.]+).*fitted=1',
                                  stderr.read_text())]
rate=sum(rates[-120:])/len(rates[-120:])
ids=(ROOT/'model/testset_40.txt').read_text().split()
raw,corrected,rows=[],[],[]
finals_equal=0
for clip in ids:
    ev=[json.loads(s) for s in (folder/f'{clip}.pl.jsonl').read_text().splitlines()]
    old=[json.loads(s) for s in (baseline/f'{clip}.pl.jsonl').read_text().splitlines()]
    fields=('phrase','capture','final','word_ends')
    a=sorted((e for e in ev if e['state']=='Committed'),key=lambda e:e['phrase'])
    b=sorted((e for e in old if e['state']=='Committed'),key=lambda e:e['phrase'])
    equal=len(a)==len(b) and all(all(x[k]==y[k] for k in fields) for x,y in zip(a,b))
    assert equal,clip
    finals_equal+=equal
    # The existing accepted corpus error/word counts remain authoritative;
    # this pass changes only latency, and verifies every final is unchanged.
    for e in ev:
        if e.get('event_only') or e['state']!='Speaking':
            continue
        assert e['latency_valid'] and e['display']=='fb'
        raw.append(e['update_ms'])
        correction=0 if e['clock_fitted'] else e['oldest_sample']*(1/rate-1/e['sample_rate'])*1000
        value=e['update_ms']-correction
        corrected.append(value)
        rows.append(dict(clip=clip,capture=e['capture'],oldest_sample=e['oldest_sample'],
            clock_fitted=e['clock_fitted'],raw_ms=e['update_ms'],startup_correction_ms=correction,
            corrected_ms=value))
q=lambda v:{k:bench.percentile(v,p) for k,p in [('p50',.5),('p95',.95),('max',1)]}
result=dict(clips=len(ids),previews=len(rows),finals_equal_clips=finals_equal,
    raw_update_ms=q(raw),corrected_update_ms=q(corrected),
    startup_sample_rate_hz=rate,startup_calibrated_previews=sum(not r['clock_fitted'] for r in rows),
    fitted_previews=sum(r['clock_fitted'] for r in rows),
    maximum_startup_correction_ms=max(abs(r['startup_correction_ms']) for r in rows),
    method='Carry mean of preceding soak final 120 fitted sample rates into each short-clip FIFO anchor; retain each ready rolling fit. Raw logs unchanged.')
(folder/'latency-corrected.json').write_text(json.dumps(result,indent=2)+'\n')
with (folder/'latency-corrected.csv').open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
print(json.dumps(result,indent=2))
