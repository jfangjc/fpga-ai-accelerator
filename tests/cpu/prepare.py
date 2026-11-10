"""Prepare only saved test clips as lossless PCM16 WAV; generate Python frontend references."""
import pathlib,sys,json,pickle
import numpy as np
import soundfile as sf
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/'model'))
from eval_wer import testset
from frontend import features
from golden_int8 import round_away,network
out=pathlib.Path('data/cpu');out.mkdir(exist_ok=True)
with open('data/int8.pkl','rb') as f:q=pickle.load(f)
rows=testset()
for i,r in enumerate(rows):
 x,sr=sf.read(r['path'],dtype='float32');sf.write(out/(r['id']+'.wav'),x,sr,subtype='PCM_16')
 if i<3:
  for mode in ['utterance','running']:
   f=features(x,mode,q.get('running_prior') if mode=='running' else None);f.tofile(out/(r['id']+'.'+mode+'.f32'))
   inp=np.clip(round_away(f/np.float32(q['input_scale'])),-128,127).astype(np.int8);inp.tofile(out/(r['id']+'.'+mode+'.i8'))
 for_dummy=0
(out/'clips.json').write_text(json.dumps(rows))
# Independent complete networks, including T=1 and odd allocated T.
for T in [1,17,101]:
 x=np.random.default_rng(T).integers(-128,128,(64,T),dtype=np.int8)
 x.tofile(out/f'network_{T}.in');network(x,q['layers'],length=T).tofile(out/f'network_{T}.out')
print('prepared',len(rows),flush=True)
