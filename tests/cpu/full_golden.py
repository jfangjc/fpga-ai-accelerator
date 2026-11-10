"""Generate independent full-utterance golden logits and validate C-feature hypotheses."""
import pathlib,pickle,sys,json
import numpy as np
sys.path.insert(0,'model')
from golden_int8 import network,round_away
from quartznet_float import greedy
root=pathlib.Path('data/cpu')
with open('data/int8.pkl','rb') as f:q=pickle.load(f)
for p in sorted(root.glob('*.utterance.c.f32')):
 uid=p.name.split('.')[0];x=np.fromfile(str(p)+'.int8',np.int8).reshape(64,-1)
 rows=json.loads((root/'clips.json').read_text());row=next(r for r in rows if r['id']==uid)
 valid=round(row['seconds']*16000)//160+1
 y=network(x,q['layers'],length=valid);y.tofile(root/(uid+'.c.golden'))
 print(uid,'C features Python network:',greedy(y),flush=True)
 inp=np.fromfile(root/(uid+'.utterance.i8'),np.int8).reshape(64,-1)
 network(inp,q['layers'],length=valid).tofile(root/(uid+'.python.golden'))
 print('full golden done',uid,flush=True)
