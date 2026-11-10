"""Compare all measured parallel-depthwise T150 layers with the schedule model."""
import csv,sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'model'))
from engine_model import depthwise_cycles
from qnc2_export import read_qnw
records=read_qnw((ROOT/'data/export/quartznet_int8.bin').read_bytes())
lookup={e['name']:records[e['id']-1][0] for e in json.loads((ROOT/'data/export/layers.json').read_text())}
rows=list(csv.DictReader(Path('depthwise150_cycles.csv').open()))
assert len(rows)==77,len(rows)
errors=[];reference=[]
for r in rows:
 d=lookup[r['name']];pred=depthwise_cycles(d[4],150*d[7],*d[6:10]);actual=int(r['cycles'])
 error=abs(pred-actual)/actual;errors.append(error)
 reference.append(dict(name=r['name'],channels=d[4],input_frames=150*d[7],output_frames=150,taps=d[6],measured_cycles=actual,model_cycles=pred))
 assert error<=.02,(r['name'],pred,actual,error)
print(f'PASS depthwise cycle model: {len(rows)} layers, max relative error {max(errors):.6%} (limit 2%)')


with open('depthwise_t150_reference.csv','w',newline='') as f:
 writer=csv.DictWriter(f,fieldnames=list(reference[0]),lineterminator=chr(10));writer.writeheader();writer.writerows(reference)


measured_total=sum(int(r['cycles']) for r in rows)
assert measured_total <= 3_000_000, measured_total
print(f'PASS depthwise T150 total: {measured_total:,} cycles (budget 3,000,000)')

