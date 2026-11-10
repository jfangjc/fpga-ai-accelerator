"""Deterministic selection and corpus WER (sum edit distances / reference words)."""
import argparse,json,pathlib,os
import numpy as np
import soundfile as sf
from frontend import features
from quartznet_float import QuartzNet,greedy

def records(split):
    cache=pathlib.Path('data')/(split+'_manifest.json')
    if cache.exists(): return json.loads(cache.read_text())
    root=pathlib.Path('data/LibriSpeech')/split; rows=[]
    for tr in sorted(root.rglob('*.trans.txt')):
        for line in tr.read_text().splitlines():
            uid,text=line.split(' ',1); wav=tr.parent/(uid+'.flac')
            rows.append(dict(id=uid,path=str(wav),text=text.lower(),seconds=sf.info(wav).duration))
    if not rows: raise FileNotFoundError(root)
    rows=sorted(rows,key=lambda r:r['id'])
    tmp=cache.with_suffix(f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(rows)); tmp.replace(cache)
    return rows

def select():
    # Deterministic duration-nearest subset: 40 different speakers if available.
    rows=records('test-clean'); used=set(); chosen=[]
    for r in sorted(rows,key=lambda r:(abs(r['seconds']-7.675),r['id'])):
        speaker=r['id'].split('-')[0]
        if speaker not in used:
            chosen.append(r); used.add(speaker)
        if len(chosen)==40: break
    if len(chosen)!=40: raise ValueError('Need 40 speakers')
    pathlib.Path('model/testset_40.txt').write_text('\n'.join(r['id'] for r in sorted(chosen,key=lambda r:r['id']))+'\n')
    return chosen

def testset():
    ids=set(pathlib.Path('model/testset_40.txt').read_text().split())
    return [r for r in records('test-clean') if r['id'] in ids]

def distance(a,b):
    prev=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        row=[i]
        for j,y in enumerate(b,1): row.append(min(row[-1]+1,prev[j]+1,prev[j-1]+(x!=y)))
        prev=row
    return prev[-1]

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--select',action='store_true'); p.add_argument('--int8'); p.add_argument('--mode',default='utterance',choices=['utterance','running']); p.add_argument('--out',default='data/wer.json'); a=p.parse_args()
    import torch
    torch.set_num_threads(4)
    rows=select() if a.select else testset(); model=QuartzNet()
    if a.int8:
        import pickle
        from golden_int8 import network,round_away
        with open(a.int8,'rb') as f: q=pickle.load(f)
    errors=words=0; results=[]
    for r in rows:
        x,sr=sf.read(r['path'],dtype='float32'); assert sr==16000
        prior=q.get('running_prior') if a.int8 and a.mode=='running' else None
        f=features(x,a.mode,state=prior); length=len(x)//160+1
        if a.int8: logits=network(np.clip(round_away(f/q['input_scale']),-128,127).astype(np.int8),q['layers'],length=length)
        else: logits=model(f,length=length)
        hyp=greedy(logits); e=distance(r['text'].split(),hyp.split()); errors+=e; words+=len(r['text'].split())
        results.append(dict(**r,hyp=hyp,errors=e)); print(r['id'],hyp,flush=True)
    report=dict(wer=errors/words,errors=errors,words=words,seconds=sum(r['seconds'] for r in rows),clips=results)
    pathlib.Path(a.out).write_text(json.dumps(report,indent=2)); print(json.dumps({k:v for k,v in report.items() if k!='clips'}))
