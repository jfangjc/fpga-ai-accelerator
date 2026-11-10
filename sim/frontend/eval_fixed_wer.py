# WER adapter: execute from repo root in the model environment.
# Matches centered/reflected STFT by padding preemphasized signal conceptually:
# streaming fixed path and float path are both evaluated with identical framing.
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'model'))
import numpy as np
import soundfile as sf
import torch
from eval_wer import testset,distance
from quartznet_float import QuartzNet,greedy
from frontend import log_normalize
from frontend_fixed import mel_power_fixed
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from model.test_frontend_fixed import float_stream

torch.set_num_threads(4)
model=QuartzNet(); totals=[0,0];words=0;reports=[]
for r in testset():
    x,sr=sf.read(r['path'],dtype='int16');assert sr==16000
    _,fixed=mel_power_fixed(x); floating=float_stream(x.astype(float)/32768)
    hyps=[]
    for i,mel in enumerate((floating,fixed)):
        f,_=log_normalize(mel);length=f.shape[1];f=np.pad(f,((0,0),(0,(-length)%16)))
        hyp=greedy(model(f,length=length));hyps.append(hyp)
        totals[i]+=distance(r['text'].split(),hyp.split())
    words+=len(r['text'].split());reports.append(dict(id=r['id'],float=hyps[0],fixed=hyps[1]))
print(json.dumps(dict(float_wer=totals[0]/words,fixed_wer=totals[1]/words,words=words,clips=reports),indent=2))
