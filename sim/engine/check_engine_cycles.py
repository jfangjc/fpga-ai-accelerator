"""Compare full-top unstalled T150 response counters to the schedule model."""
import csv
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'model'))
from engine_model import s1_t150_cycles
rows = list(csv.DictReader(Path('engine_t150_cycles.csv').open()))
assert len(rows) == 94, len(rows)
for row in rows:
    expected = s1_t150_cycles(int(row['ci']), int(row['co']))
    actual = int(row['reported_cycles'])
    assert expected == actual, (row['name'], expected, actual)
print(f'PASS full-engine T150 cycle model: {len(rows)} layers, zero cycle difference')
