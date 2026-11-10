"""Plain Torch evaluation graph loaded from the trusted NVIDIA .nemo archive."""
import io, tarfile
import numpy as np
import torch
import torch.nn.functional as F
import yaml

def load(path='data/quartznet.nemo'):
    with tarfile.open(path) as t:
        cfg=yaml.safe_load(t.extractfile('./model_config.yaml'))
        sd=torch.load(io.BytesIO(t.extractfile('./model_weights.ckpt').read()),
                      map_location='cpu', weights_only=True)
    return cfg,sd

class QuartzNet:
    def __init__(self, path='data/quartznet.nemo', folded=True):
        self.cfg,self.sd=load(path); self.folded=folded; self.layers=[]
        source='input'; channels=64
        for b,cfg in enumerate(self.cfg['encoder']['jasper']):
            block_input=source; prefix=f'encoder.encoder.{b}.'
            sep=cfg.get('separable',False); step=5 if sep else 4
            for r in range(cfg['repeat']):
                base=prefix+f'mconv.{r*step}'; last=r==cfg['repeat']-1
                if sep:
                    source=self.conv(f'b{b}.r{r}.dw',source,base+'.conv',
                                     channels,cfg['stride'][0] if r==0 else 1,cfg['dilation'][0])
                    base=prefix+f'mconv.{r*step+1}'
                bn=prefix+f'mconv.{r*step+(2 if sep else 1)}'
                source=self.conv(f'b{b}.r{r}.pw',source,base+'.conv',1,1,1,bn,
                                 relu=not (last and cfg['residual']))
                channels=cfg['filters']
            if cfg['residual']:
                res=self.conv(f'b{b}.res',block_input,prefix+'res.0.0.conv',1,1,1,prefix+'res.0.1')
                self.layers.append(dict(name=f'b{b}.add',op='add',inputs=[source,res],relu=True))
                source=f'b{b}.add'
        self.conv('logits',source,'decoder.decoder_layers.0',1,1,1)

    def conv(self,name,source,key,groups,stride,dilation,bn=None,relu=False):
        w=self.sd[key+'.weight'].clone(); bias=self.sd.get(key+'.bias',torch.zeros(w.shape[0])).clone()
        raww=w.clone(); rawb=bias.clone()
        if bn:
            a=self.sd[bn+'.weight']/torch.sqrt(self.sd[bn+'.running_var']+1e-3)
            bias=(bias-self.sd[bn+'.running_mean'])*a+self.sd[bn+'.bias']; w=w*a[:,None,None]
        self.layers.append(dict(name=name,op='conv',inputs=[source],w=w,b=bias,
            raww=raww,rawb=rawb,bn=bn,groups=groups,stride=stride,dilation=dilation,
            padding=dilation*(w.shape[2]-1)//2,relu=relu))
        return name

    @torch.inference_mode()
    def __call__(self,x, capture=False, length=None):
        x=torch.as_tensor(x,dtype=torch.float32)
        if x.ndim==2: x=x[None]
        tensors={'input':x}; lengths={'input':length or x.shape[-1]}
        for l in self.layers:
            ins=[tensors[n] for n in l['inputs']]; n=lengths[l['inputs'][0]]
            if l['op']=='add': y=ins[0]+ins[1]
            else:
                a=ins[0].clone(); a[:,:,n:]=0
                y=F.conv1d(a,l['w'] if self.folded else l['raww'],l['b'] if self.folded else l['rawb'],
                           l['stride'],l['padding'],l['dilation'],l['groups'])
                n=(n+2*l['padding']-l['dilation']*(l['w'].shape[2]-1)-1)//l['stride']+1
                if not self.folded and l['bn']:
                    k=l['bn']; y=F.batch_norm(y,self.sd[k+'.running_mean'],self.sd[k+'.running_var'],
                       self.sd[k+'.weight'],self.sd[k+'.bias'],training=False,eps=1e-3)
            if l['relu']: y=y.relu()
            tensors[l['name']]=y; lengths[l['name']]=n
        return (tensors,lengths) if capture else y[:,:,:n].numpy()[0]

def greedy(logits,vocab=None):
    vocab=vocab or list(" abcdefghijklmnopqrstuvwxyz'")
    ids=np.asarray(logits).argmax(axis=0); prev=-1; out=[]
    for i in ids:
        if i != prev and i<len(vocab): out.append(vocab[i])
        prev=i
    return ''.join(out).strip()
