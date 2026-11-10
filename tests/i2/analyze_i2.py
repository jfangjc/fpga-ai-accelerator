"""Summarise saved board timings; engine preparation overlaps IRQ waits."""
import csv
import json
import pathlib
import re
import statistics
import sys

d = pathlib.Path(sys.argv[1])
main = pathlib.Path(sys.argv[2])
text = (d / 'probe-instrumented.log').read_text()
network = dict((k, int(v)) for k, v in re.findall(r'(\w+)=(\d+)', next(x for x in text.splitlines() if x.startswith('NETWORK '))))
transport = dict((k, int(v)) for k, v in re.findall(r'(\w+)=(\d+)', next(x for x in text.splitlines() if x.startswith('TRANSPORT '))))
lines = [x for x in text.splitlines() if re.match(r'^\d+,', x)]
rows = list(csv.DictReader(['layer,cycles,submit_irq_ns,irq_ready_ns,total_ns,starvation,terms'] + lines))
rows = [{k: int(v) for k, v in r.items()} for r in rows]
assert len(rows) == 186 and sum(r['cycles'] for r in rows) == network['cycles']
layers = json.loads((main / 'data/export/layers.json').read_text())
groups = {}
for r, layer in zip(rows, layers):
    r['name'] = layer['name']
    kind = 'depthwise' if '.dw' in r['name'] else 'add' if '.add' in r['name'] else 'pointwise'
    r['kind'] = kind
    g = groups.setdefault(kind, dict(commands=0, cycles=0, command_ns=0))
    g['commands'] += 1
    g['cycles'] += r['cycles']
    g['command_ns'] += r['total_ns']
summary = dict(network=network, transport=transport, groups=groups)
summary['host_remaining_ns'] = network['wall_ns'] - network['sync_ns'] - transport['dma_submit_ns'] - transport['irq_wait_ns']
summary['sum_command_ns'] = sum(r['total_ns'] for r in rows)
summary['outside_exchange_ns'] = network['wall_ns'] - summary['sum_command_ns']
summary['command_ns'] = dict(min=min(r['total_ns'] for r in rows), median=statistics.median(r['total_ns'] for r in rows), max=max(r['total_ns'] for r in rows))
summary['sum_submit_irq_ns'] = sum(r['submit_irq_ns'] for r in rows)
summary['sum_irq_ready_ns'] = sum(r['irq_ready_ns'] for r in rows)
summary['sum_starvation'] = sum(r['starvation'] for r in rows)
print(json.dumps(summary, indent=2))
(d / 'timing-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
with (d / 'network-3s.csv').open('w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator='\n')
    w.writeheader()
    w.writerows(rows)
