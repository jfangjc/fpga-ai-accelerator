"""Integer-only network primitives. int64 temporaries detect int32 overflow."""
import numpy as np

def round_away(x):
    return (np.sign(x)*np.floor(np.abs(x)+.5)).astype(np.int64)

def checked(x):
    if np.any(x>2147483647) or np.any(x< -2147483648): raise OverflowError('int32 accumulator')
    return x.astype(np.int32)

def requant(acc,mult,shift,lo=-128,hi=127):
    """One rounding after signed 64-bit product; ties away from zero."""
    p=acc.astype(np.int64)*np.asarray(mult,np.int64)[:,None]
    raw_s=31+np.asarray(shift,np.int64)[:,None]
    if np.any(raw_s<1): raise ValueError('unsupported left shift')
    s=np.minimum(raw_s,63)
    q=np.sign(p)*((np.abs(p)+(np.int64(1)<<(s-1)))>>s)
    q=np.where(raw_s>=64,0,q)
    dtype=np.uint8 if lo==0 and hi==255 else (np.int8 if lo==-128 and hi==127 else np.int32)
    return np.clip(q,lo,hi).astype(dtype)

def conv_acc(x,w,b,stride=1,padding=0,dilation=1,groups=1):
    x=np.pad(x.astype(np.int64),((0,0),(padding,padding)))
    o,ci,k=w.shape; t=(x.shape[1]-dilation*(k-1)-1)//stride+1
    a=np.zeros((o,t),np.int64)
    for j in range(k):
        v=x[:,j*dilation:j*dilation+t*stride:stride]
        if groups==1: a+=w[:,:,j].astype(np.int64)@v
        elif groups==o and ci==1: a+=w[:,0,j,None].astype(np.int64)*v
        else: raise ValueError('unsupported grouped convolution')
    return checked(a+np.asarray(b,np.int64)[:,None])

def layer(x,l):
    acc=conv_acc(x,l['w'],l['b'],l['stride'],l['padding'],l['dilation'],l['groups'])
    return requant(acc,l['mult'],l['shift'],0 if l['relu'] else -128,255 if l['relu'] else 127)

def network(x,layers,capture=False,length=None):
    tensors={'input':x}; lengths={'input':length or x.shape[1]}
    for l in layers:
        ins=[tensors[n] for n in l['inputs']]; n=lengths[l['inputs'][0]]
        if l['op']=='add':
            a=requant(ins[0].astype(np.int32),l['mult0'],l['shift0'],-32768,32767)
            b=requant(ins[1].astype(np.int32),l['mult1'],l['shift1'],-32768,32767)
            y=np.clip(a.astype(np.int32)+b.astype(np.int32),0,255).astype(np.uint8)
        else:
            a=ins[0].copy(); a[:,n:]=0; y=layer(a,l)
            n=(n+2*l['padding']-l['dilation']*(l['w'].shape[2]-1)-1)//l['stride']+1
        tensors[l['name']]=y; lengths[l['name']]=n
    return (tensors,lengths) if capture else y[:,:n]
