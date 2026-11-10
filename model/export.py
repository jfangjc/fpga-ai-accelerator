"""QNW1 packed blob and independent per-layer vectors; see weights_format.md."""
import argparse,json,pathlib,pickle,struct
import numpy as np
import soundfile as sf
from frontend import features
from eval_wer import testset
from golden_int8 import network,round_away

def tiled(w):
    """[out_tile][k_tile][k16][out32], K = channel*kernel + tap."""
    w=w.reshape(w.shape[0],-1); o,k=w.shape
    p=np.pad(w,((0,(-o)%32),(0,(-k)%16)))
    return p.reshape(p.shape[0]//32,32,p.shape[1]//16,16).transpose(0,2,3,1).copy()

def export(q,out='data/export'):
    out=pathlib.Path(out); out.mkdir(parents=True,exist_ok=True)
    ls=q['layers']; payload=bytearray(); descriptors=[]; index={'input':0}
    base=64+128*len(ls)
    def append(a):
        while (base+len(payload))%64: payload.append(0)
        offset=base+len(payload); b=np.asarray(a).tobytes(); payload.extend(b); return offset,len(b)
    for i,l in enumerate(ls,1):
        index[l['name']]=i; fields=[0]*32
        fields[:6]=[1 if l['op']=='conv' else 2,index[l['inputs'][0]],index[l['inputs'][1]] if len(l['inputs'])==2 else 0,int(l['relu']),0,0]
        if l['op']=='conv':
            o,c,k=l['w'].shape; fields[4:12]=[o,c,k,l['stride'],l['dilation'],l['padding'],l['groups'],0]
            for slot,key,a in [(12,'w',tiled(l['w'])),(14,'b',l['b'].astype('<i4')),(16,'mult',l['mult'].astype('<i4')),(18,'shift',l['shift'].astype('<i4'))]: fields[slot:slot+2]=append(a)
        else:
            fields[4]=len(l['mult0'])
            for slot,key in [(12,'mult0'),(14,'shift0'),(16,'mult1'),(18,'shift1')]: fields[slot:slot+2]=append(l[key].astype('<i4'))
        descriptors.append(struct.pack('<32I',*fields))
    header=struct.pack('<4s7If',b'QNW1',1,64,len(ls),128,64,base+len(payload),0,q['input_scale']).ljust(64,b'\0')
    (out/'quartznet_int8.bin').write_bytes(header+b''.join(descriptors)+payload)
    (out/'layers.json').write_text(json.dumps([dict(id=i+1,name=l['name'],scale=l['scale']) for i,l in enumerate(ls)],indent=2))
    if 'running_prior' in q:
        (out/'running_prior.json').write_text(json.dumps({k:v.tolist() if hasattr(v,'tolist') else v for k,v in q['running_prior'].items()},indent=2))
    return out

def vectors(q,out):
    dest=out/'vectors'; dest.mkdir(exist_ok=True); manifest=[]
    rows=testset()[:3]
    for clip,r in enumerate(rows):
        x,sr=sf.read(r['path'],dtype='float32'); assert sr==16000
        # Deliberately test odd T and short boundaries as well as a full clip.
        if clip==1: x=x[:160*101+17]
        if clip==2: x=x[:160*17+3]
        f=features(x); inp=np.clip(round_away(f/q['input_scale']),-128,127).astype(np.int8)
        ts,lengths=network(inp,q['layers'],capture=True,length=len(x)//160+1)
        for l in q['layers']:
            stem=f'clip{clip}_{l["name"]}'; entry=dict(layer=l['name'],clip=r['id'],inputs=[],output=None)
            for j,name in enumerate(l['inputs']+[l['name']]):
                a=ts[name].copy()
                if l['op']=='conv' and name!=l['name']: a[:,lengths[name]:]=0
                suffix='out' if name==l['name'] else ('in' if j==0 else 'in1')
                file=stem+'_'+suffix+'.bin'; a.tofile(dest/file)
                meta=dict(file=file,shape=list(a.shape),dtype=str(a.dtype),valid_frames=lengths[name])
                if suffix=='out': entry['output']=meta
                else: entry['inputs'].append(meta)
            manifest.append(entry)
    (dest/'manifest.json').write_text(json.dumps(manifest,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--model',default='data/int8.pkl'); p.add_argument('--vectors',action='store_true'); a=p.parse_args()
    with open(a.model,'rb') as f: q=pickle.load(f)
    out=export(q)
    if a.vectors: vectors(q,out)
