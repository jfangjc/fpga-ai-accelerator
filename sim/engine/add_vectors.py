"""Residual manifest and adversarial arithmetic full-top vectors."""
import json, struct, sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'model'))
from qnc2_export import read_qnw,run_layer
from golden_int8 import requant
records=read_qnw((ROOT/'data/export/quartznet_int8.bin').read_bytes())
lookup={e['name']:records[e['id']-1] for e in json.loads((ROOT/'data/export/layers.json').read_text())}
directory=ROOT/'data/export/vectors'
cases=[];timed=[];seen=set()
def add(target,name,r,x,b,valid,y=None):
 d,a=r;t=x.shape[1]
 if y is None:
  m,s,m1,s1=[np.frombuffer(z,np.int32) for z in a]
  y=np.clip(requant(x.astype(np.int32),m,s,-32768,32767)+requant(b.astype(np.int32),m1,s1,-32768,32767),0,255).astype(np.uint8)
 target.append((name,run_layer(r,x.tobytes(),t,valid,branch1=b.tobytes()),y.tobytes(),0,t,valid))
for e in json.loads((directory/'manifest.json').read_text()):
 r=lookup[e['layer']]
 if r[0][0]!=2:continue
 xs=[np.frombuffer((directory/z['file']).read_bytes(),np.dtype(z['dtype'])).reshape(z['shape']) for z in e['inputs']]
 z=e['output'];y=np.frombuffer((directory/z['file']).read_bytes(),np.uint8).reshape(z['shape'])
 add(cases,e['clip']+'/'+e['layer'],r,*xs,e['inputs'][0]['valid_frames'],y)
 if e['layer'] not in seen:
  seen.add(e['layer']);add(timed,e['layer'],r,*[np.resize(x,(r[0][4],150)) for x in xs],150)
manifest=len(cases)
rng=np.random.default_rng(89)
for t in [1,17,31]:
 for valid in [0,t//2,t]:
  c=8;d=[2,0,0,1,c]+[0]*27
  a=[np.array(z,np.int32).tobytes() for z in [[2147483647]*c,[-30,-20,-1,0,1,32,33,2147483647],[1073741824]*c,[-30,-20,-1,0,1,32,33,2147483647]]]
  x=rng.integers(-128,128,(c,t),dtype=np.int8);b=rng.integers(-128,128,(c,t),dtype=np.int8)
  x[0]=127;b[0]=-128;x[1]=-128;b[1]=127
  add(cases,f'edge/{t}/{valid}',(d,a),x,b,valid)
name,cmd,y,_,t,v=cases[-1];bad=bytearray(cmd);struct.pack_into('<I',bad,36,0)
cases.extend([('malformed',bytes(bad),b'',4,0,0),('recovery',cmd,y,0,t,v)])
for filename,entries in [('add.bin',cases),('add150.bin',timed)]:
 with open(filename,'wb') as f:
  f.write(struct.pack('<I',len(entries)))
  for name,cmd,y,status,t,v in entries:
   label=name.encode();f.write(struct.pack('<6I',len(label),len(cmd),len(y),status,t,v));f.write(label);f.write(cmd);f.write(y)
print(f'Packaged {manifest} add manifest + 9 synthetic + malformed/recovery; {len(timed)} T150 layers')

