"""Per-tensor percentile calibration on dev-clean ONLY."""
import argparse,pickle,pathlib
import numpy as np
import soundfile as sf
import torch
from quartznet_float import QuartzNet
from frontend import features,mel_power,log_normalize
from eval_wer import records
from golden_int8 import round_away,checked

def multiplier(r):
    mant,e=np.frexp(np.asarray(r,dtype=np.float64)); m=round_away(mant*2**31)
    overflow=m==2**31; m=np.where(overflow,m//2,m); e=e+overflow
    return m.astype(np.int32),(-e).astype(np.int32)

def calibrate(count=100,percentile=99.99):
    torch.set_num_threads(4); model=QuartzNet(); ranges={}; used=[]
    log_sum=np.zeros(64); log_sumsq=np.zeros(64); frames=0
    for r in records('dev-clean')[:count]:
        x,sr=sf.read(r['path'],dtype='float32'); assert sr==16000
        mel=mel_power(x); logs=np.log(mel+np.float32(2**-24)).astype(np.float64)
        log_sum+=logs.sum(1); log_sumsq+=(logs**2).sum(1); frames+=logs.shape[1]
        f,_=log_normalize(mel); f=np.pad(f,((0,0),(0,(-f.shape[1])%16)))
        ts,_=model(f,capture=True,length=len(x)//160+1)
        for name,v in ts.items():
            v=v.numpy(); bound=float(np.percentile(np.abs(v),percentile))
            ranges[name]=max(ranges.get(name,0),bound)
        used.append(r['id']); print('calibrate',r['id'],flush=True)
    scales={'input':max(ranges['input'],1e-8)/127}; layers=[]
    for l in model.layers:
        q={k:v for k,v in l.items() if k not in ('w','b','raww','rawb','bn')}
        scale=max(ranges[l['name']],1e-8)/(255 if l['relu'] else 127); scales[l['name']]=scale
        if l['op']=='conv':
            w=l['w'].numpy(); ws=np.maximum(np.abs(w).max((1,2))/127,1e-12)
            # Nearly-zero folded BN channels can have nonzero constant biases.
            # Reserve half the accumulator range for bias instead of overflowing.
            sx=scales[l['inputs'][0]]
            ws=np.maximum(ws,np.abs(l['b'].numpy())/(sx*2**30))
            q['w']=np.clip(round_away(w/ws[:,None,None]),-127,127).astype(np.int8)
            accscale=sx*ws
            q['b']=checked(round_away(l['b'].numpy()/accscale))
            q['mult'],q['shift']=multiplier(accscale/scale)
        else:
            channels=model.sd['encoder.encoder.'+l['name'][1:].split('.')[0]+'.res.0.1.weight'].shape[0]
            for i,src in enumerate(l['inputs']):
                q['mult'+str(i)],q['shift'+str(i)]=multiplier(np.full(channels,scales[src]/scale))
        q['scale']=scale; layers.append(q)
    mean=log_sum/frames; variance=np.maximum(log_sumsq/frames-mean**2,1e-8)
    prior=dict(count=100,mean=mean,m2=variance*99)
    return dict(input_scale=scales['input'],layers=layers,calibration_ids=used,percentile=percentile,running_prior=prior)

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--count',type=int,default=100); p.add_argument('--percentile',type=float,default=99.99); p.add_argument('--out',default='data/int8.pkl'); a=p.parse_args()
    q=calibrate(a.count,a.percentile)
    with open(a.out,'wb') as f: pickle.dump(q,f)
