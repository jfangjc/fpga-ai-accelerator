from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import numpy as np
from model.frontend_fixed import beamform_fixed
rng=np.random.default_rng(1234)
rows=[]
for d1,d2,gain in ((0,0,16384),(1,5,-8192),(17,63,32767),(255,128,-32768)):
    m=rng.integers(-131072,131072,(2,1024))
    out=beamform_fixed(m[0],m[1],d1,d2,gain)
    rows.extend((int(a)&0x3ffff)|((int(b)&0x3ffff)<<18)|((int(c)&0x3ffff)<<36) for a,b,c in zip(*m,out))
out=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'build/sim/frontend'  # build directory
out.mkdir(parents=True,exist_ok=True)
(out/'beam.mem').write_text(''.join(f'{v:014x}\n' for v in rows))
