"""Depthwise full-top commands: immutable manifest oracles and synthetic edges."""
import json,struct,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'model'))
from qnc2_export import read_qnw,run_layer
from golden_int8 import conv_acc,requant
records=read_qnw((ROOT/'data/export/quartznet_int8.bin').read_bytes())
lookup={e['name']:records[e['id']-1] for e in json.loads((ROOT/'data/export/layers.json').read_text())}
directory=ROOT/'data/export/vectors'
cases=[]; timed=[];seen=set()
def oracle(record,x,valid):
 d,a=record;c=d[4];k=d[6]
 w=np.frombuffer(a[0],np.int8).reshape(-1,(k+15)//16,16,32).transpose(0,3,1,2).reshape(-1,((k+15)//16)*16)[:c,:k].copy()[:,None,:]
 x=x.copy();x[:,valid:]=0
 b,m,s=[np.frombuffer(z,np.int32) for z in a[1:]]
 return requant(conv_acc(x,w,b,d[7],d[9],d[8],c),m,s)
def add(target,name,record,x,valid,y=None):
 d,_=record;t=x.shape[1];v=max(0,(valid+2*d[9]-d[8]*(d[6]-1)-1)//d[7]+1)
 y=oracle(record,x,valid) if y is None else y
 target.append((name,run_layer(record,x.tobytes(),t,valid,signed=x.dtype==np.int8),y.tobytes(),0,y.shape[1],v))
for e in json.loads((directory/'manifest.json').read_text()):
 r=lookup[e['layer']];d,a=r
 if d[0]!=1 or d[5]!=1 or d[10]!=d[4]:continue
 src=e['inputs'][0];dst=e['output'];x=np.frombuffer((directory/src['file']).read_bytes(),np.dtype(src['dtype'])).reshape(src['shape'])
 y=np.frombuffer((directory/dst['file']).read_bytes(),np.int8).reshape(dst['shape'])
 add(cases,e['clip']+'/'+e['layer'],r,x,src['valid_frames'],y)
 if e['layer'] not in seen:
  seen.add(e['layer']);tin=150*d[7];xp=np.resize(x,(d[4],tin)).copy();add(timed,e['layer'],r,xp,tin)
manifest=len(cases);rng=np.random.default_rng(88)
for t,valid,k,stride,dilation,pad in [(1,1,1,1,1,0),(17,11,3,1,1,1),(31,0,5,2,2,4),(17,15,87,2,2,86),(19,18,87,1,2,86),
                                     (35,35,3,2,2,2),(35,31,3,1,2,1),(800,799,87,2,2,86),(255,211,87,1,2,86),(800,800,1,1,1,0)]:
 for signed in (False,True):
  c={1:3,17:9,31:31,19:33,35:3,800:9,255:33}[t];d=[1,0,0,0,c,1,k,stride,dilation,pad,c]+[0]*21
  w=rng.integers(-127,128,(c,k),dtype=np.int8);tile=np.zeros(((c+31)//32,(k+15)//16,16,32),np.int8)
  for ch in range(c):
   for tap in range(k):tile[ch//32,tap//16,tap%16,ch%32]=w[ch,tap]
  arrays=[tile.tobytes()]+[z.tobytes() for z in [rng.integers(-100,100,c,dtype=np.int32),np.full(c,1<<30,np.int32),np.full(c,7,np.int32)]]
  x=rng.integers(-128 if signed else 0,128 if signed else 256,(c,t),dtype=np.int16).astype(np.int8 if signed else np.uint8)
  add(cases,f'edge/{t}/{k}/s{stride}/d{dilation}/p{pad}/{signed}',(d,arrays),x,valid)
# Exercise the deferred P-register overflow check independently of the
# final sum: the first nonzero prefix overflows, the next term cancels it.
# Both signs, first/final overflow and a safe boundary/recovery are covered.
for signed in (False,True):
 for scenario in ('cancel','final','safe'):
  d=[1,0,0,0,1,1,3,1,1,0,1]+[0]*21
  tile=np.zeros((1,1,16,32),np.int8)
  tile[0,0,:3,0]=[127,-127,0] if scenario!='final' else [0,0,127]
  bound=-(1<<31) if signed else (1<<31)-1
  distance=40000 if scenario=='safe' else 100
  b=bound+distance if signed else bound-distance
  arrays=[tile.tobytes(),np.array([b],np.int32).tobytes(),
          np.array([1<<30],np.int32).tobytes(),np.array([7],np.int32).tobytes()]
  x=np.full((1,3),-128 if signed else 255,np.int8 if signed else np.uint8)
  record=(d,arrays);cmd=run_layer(record,x.tobytes(),3,3,signed=signed)
  y=oracle(record,x,3).tobytes() if scenario=='safe' else b''
  cases.append((f'overflow/{signed}/{scenario}',cmd,y,0 if scenario=='safe' else 5,1,1))
name,cmd,y,_,t,v=cases[-1];bad=bytearray(cmd);struct.pack_into('<I',bad,44,88)
cases.extend([('malformed',bytes(bad),b'',4,0,0),('recovery',cmd,y,0,t,v)])
for filename,entries in [('depthwise.bin',cases),('depthwise150.bin',timed)]:
 with open(filename,'wb') as f:
  f.write(struct.pack('<I',len(entries)))
  for name,cmd,y,status,t,v in entries:
   label=name.encode();f.write(struct.pack('<6I',len(label),len(cmd),len(y),status,t,v));f.write(label);f.write(cmd);f.write(y)
print(f'Packaged {manifest} depthwise manifest + 26 synthetic + malformed/recovery; {len(timed)} T150 layers')



