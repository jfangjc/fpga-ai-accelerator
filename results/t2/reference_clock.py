"""Read-only timekeeping diagnostic; does not access FPGA registers."""
import json
import sys
import time
from pathlib import Path

rows = []
for i in range(int(sys.argv[1]) if len(sys.argv)>1 else 180):
    a = time.monotonic_ns()
    raw = time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
    b = time.monotonic_ns()
    rows.append(dict(monotonic_ns=(a+b)/2, raw_ns=raw, bracket_ns=b-a))
    time.sleep(1)
Path('reference-clock.json').write_text(json.dumps(rows, indent=2)+'\n')
x = [r['raw_ns']-rows[0]['raw_ns'] for r in rows]
y = [r['monotonic_ns']-rows[0]['monotonic_ns'] for r in rows]
xm, ym = sum(x)/len(x), sum(y)/len(y)
slope = sum((a-xm)*(b-ym) for a,b in zip(x,y))/sum((a-xm)**2 for a in x)
print(json.dumps(dict(seconds=x[-1]/1e9, monotonic_vs_raw_ppm=(slope-1)*1e6),indent=2))
