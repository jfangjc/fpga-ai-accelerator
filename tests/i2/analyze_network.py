"""Summarise the RUN_NETWORK probe log (tests/i2/i2_probe, paired CPU/PL 3 s windows).

Usage: python tests/i2/analyze_network.py PROBE.LOG [OUT.json]
Engine cycles (100 MHz) overlap the IRQ wait; host_remaining = pl - sync -
DMA submit - IRQ wait (copies, encoding, validation, polling).
"""
import csv
import json
import statistics
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text().splitlines()
start = next(i for i, x in enumerate(text) if x.startswith('repeat,'))
rows = []
for line in text[start:]:
    if line.startswith('PASS'):
        break
    rows.append(line)
rows = [{k: int(v) for k, v in r.items()} for r in csv.DictReader(rows)]
def percentile(values, q):
    values = sorted(values)
    position = (len(values)-1)*q
    lo = int(position)
    return values[lo] + (values[min(lo+1, len(values)-1)]-values[lo])*(position-lo)


ms = lambda key: [r[key]/1e6 for r in rows]
rows_ms = {k: ms(k) for k in rows[0] if k.endswith('_ns')}
rows_ms['engine_ms'] = [r['cycles']/1e5 for r in rows]
out = dict(repeats=len(rows), cycles=sorted({r['cycles'] for r in rows}),
           sync_calls=sorted({r['sync_calls'] for r in rows}))
for k, v in rows_ms.items():
    out[k.replace('_ns', '_ms')] = dict(median=statistics.median(v), p90=percentile(v, .9), min=min(v), max=max(v))
speedups = [c/p for c, p in zip(ms('cpu_ns'), ms('pl_ns'))]
out['speedup'] = dict(median_of_pairs=statistics.median(speedups), min=min(speedups),
                      max=max(speedups),
                      ratio_of_medians=statistics.median(ms('cpu_ns'))/statistics.median(ms('pl_ns')))
with Path(sys.argv[1]).with_name('paired-3s.csv').open('w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
assert len(rows) >= 20
assert len([x for x in text if x.startswith('PASS')]) >= 8
out['pass_lines'] = [x for x in text if x.startswith('PASS')]
print(json.dumps(out, indent=2))
if len(sys.argv) > 2:
    Path(sys.argv[2]).write_text(json.dumps(out, indent=2)+'\n')
