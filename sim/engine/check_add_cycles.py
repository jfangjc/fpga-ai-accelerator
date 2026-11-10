"""Compare all residual T150 measured counters with the exact model."""
import csv,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'model'))
from engine_model import add_t150_cycles
from qnc2_export import read_qnw
records=read_qnw((ROOT/'data/export/quartznet_int8.bin').read_bytes())
lookup={e['name']:records[e['id']-1][0][4] for e in json.loads((ROOT/'data/export/layers.json').read_text())}
rows=list(csv.DictReader(open('add150_cycles.csv')))
assert len(rows)==15
for row in rows:
 expected=add_t150_cycles(lookup[row['name']])
 assert int(row['cycles'])==expected,(row,expected)
print('PASS residual T150: 15 exact cycle matches')
