"""Summarize accepted alternating pairs; nearest-rank p90, no warmups in log."""
import hashlib
import json
import math
import pathlib
import re
import statistics
import sys

root = pathlib.Path(sys.argv[1])
rows = []
for line in (root / 'paired.log').read_text().splitlines():
    if line.startswith('PASS pair='):
        row = dict(re.findall(r'(\w+)=([^ ]+)', line))
        rows.append({k: float(row[k]) for k in ('cpu_ms', 'pl_ms', 'cycles', 'sync_ms')})
if len(rows) < 20:
    raise ValueError(f'only {len(rows)} accepted pairs')
def summary(values):
    return {'median': statistics.median(values), 'p90': sorted(values)[math.ceil(.9 * len(values))-1]}
result = {'pairs': len(rows), 'cpu_threads': 4, 'input_frames': 300,
          'warmup_pairs_excluded': 2, 'p90_method': 'nearest rank',
          'cpu_ms': summary([r['cpu_ms'] for r in rows]),
          'pl_ms': summary([r['pl_ms'] for r in rows]),
          'paired_speedup': summary([r['cpu_ms']/r['pl_ms'] for r in rows]),
          'engine_preparation_ms_at_150MHz': summary([r['cycles']/150000 for r in rows]),
          'sync_ms': summary([r['sync_ms'] for r in rows])}
i2 = (root / 'i2.log').read_text().splitlines()
accepted = [dict(re.findall(r'(\w+)=([^ ]+)', line)) for line in i2 if line.startswith('PASS pair=')]
if len(accepted) != 12:
    raise ValueError(f'expected 12 I2 windows, got {len(accepted)}')
if sum(line.startswith('PASS I2 logits+CTC') for line in i2) != 12:
    raise ValueError('original I2 tool did not accept all 12 windows')
result['i2'] = {'windows': len(accepted), 'logit_bytes': sum(int(r['logits']) for r in accepted),
                'ctc_identical': len(accepted), 'network_windows': sum(r['mode'] == 'network' for r in accepted),
                'fallback_windows': sum(r['mode'] == 'layer' for r in accepted)}
result['log_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in root.glob('*.log')}
(root / 'summary.json').write_text(json.dumps(result, indent=2) + '\n', newline='\n')
print(json.dumps(result, indent=2))
