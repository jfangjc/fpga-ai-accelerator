"""Check network accounting and the serialized estimate against simulation."""
import re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'model'))
from qnc2_export import read_program
from engine_model import serialized_network_cycles
records=read_program((ROOT/'data/export/quartznet_qnc2.bin').read_bytes())
text=Path('network.log').read_text()  # written by make check in the build directory
rows=re.findall(r'CYCLES network/T(\d+)/valid\d+ total=(\d+) compute=(\d+) weight=(\d+) transition=(\d+)',text)
assert len(rows)==8 and {int(r[0]) for r in rows}=={32,48,112,150,300,304,320,640}, 'incomplete network cycle measurements'
for t,total,compute,weight,transition in rows:
    t,total,compute,weight,transition=map(int,(t,total,compute,weight,transition))
    assert total==compute+weight+transition,(t,total,compute,weight,transition)
    if t==300:
        assert total<=10_500_000,('3-second window cycle budget exceeded',total)
        predicted=serialized_network_cycles(records,t)['cycles']
        print(f'T150 output / T300 input: measured {total}, predicted {predicted}, error {100*(predicted-total)/total:.3f}%')
        assert abs(predicted-total)/total<.03,'network model exceeds 3% tolerance'
        print(f'compute={compute}, weight waits={weight}, transitions={transition}; {total/100000:.5f} ms at 100 MHz; {total/150000:.5f} ms at 150 MHz')
print(f'PASS {len(rows)} network counter sums')
