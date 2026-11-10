"""Verify collected I2 RUN_NETWORK evidence, using absolute external data paths.

Usage: python tests/i2/verify_i2_network.py MAIN_CHECKOUT BOARD_DUMP EVIDENCE_DIR
"""
import csv
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

root = Path(__file__).resolve().parents[2]
main, dump, out = map(Path, sys.argv[1:])
assert main.is_absolute()
sys.path.insert(0, str(root / 'model'))
spec = importlib.util.spec_from_file_location('i1', root / 'tests/i1/i1.py')
i1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(i1)
i1.ROOT, i1.OUT = main, main / 'data/i1'
# Filename is the fixture key used by the inherited I1 comparer.
files = []
for mode in ('bulk', 'paced'):
    dest = dump / f'1089-134691-0023.{mode}.bin'
    dest.write_bytes((dump / f'frontend.{mode}.bin').read_bytes())
    files.append(str(dest))
i1.compare(SimpleNamespace(files=files, json=str(out / 'frontend-results.json')))
chunks = list(csv.reader((dump / 'chunks.tsv').read_text().splitlines(), delimiter='\t'))
lines = [s for s in (dump / 'i2.log').read_text().splitlines() if s.startswith('PASS I2')]
assert len(chunks) == len(lines) >= 10
rows = []
for (name, frames, valid), line in zip(chunks, lines):
    fields = dict(s.split('=', 1) for s in line.split() if '=' in s)
    row = dict(input=Path(name).name, input_frames=int(frames), input_valid=int(valid), **fields)
    row['logit_bytes'] = 29 * int(fields['T'])
    row['engine_ms'] = int(fields['cycles']) / 100000
    assert fields['mode'] == ('network' if int(frames) <= 640 else 'layer')
    rows.append(row)
assert {640, 656, 1552} <= {r['input_frames'] for r in rows}
with (out / 'windows.csv').open('w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
result = dict(windows=len(rows), logit_bytes=sum(r['logit_bytes'] for r in rows),
              network_windows=sum(r['mode'] == 'network' for r in rows),
              layer_windows=sum(r['mode'] == 'layer' for r in rows))
(out / 'acceptance.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result))