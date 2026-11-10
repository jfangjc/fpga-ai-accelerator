"""Bit-exact streaming front end; input signed 18-bit (int16 promoted by 4)."""
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]/'rtl/frontend'
def coefficients():
    h=np.rint(np.hanning(320)*2**18).astype(np.int64)
    w=np.rint(np.exp(-2j*np.pi*np.arange(256)/512)*2**22)
    def hzmel(f): return np.where(f<1000,f/(200/3),15+np.log(np.maximum(f,1)/1000)/(np.log(6.4)/27))
    m=np.linspace(0,float(hzmel(np.array(8000))),66)
    hz=np.where(m<15,m*(200/3),1000*np.exp((m-15)*np.log(6.4)/27))
    f=np.arange(257)*16000/512
    fb=np.maximum(0,np.minimum((f[None,:]-hz[:-2,None])/(hz[1:-1]-hz[:-2])[:,None],(hz[2:,None]-f[None,:])/(hz[2:]-hz[1:-1])[:,None]))* (2/(hz[2:]-hz[:-2]))[:,None]
    return h,w.real.astype(np.int64),w.imag.astype(np.int64),np.rint(fb*2**28).astype(np.int64)
def mel_power_fixed(samples_int16_or_int18):
    x=np.asarray(samples_int16_or_int18)
    x=x.astype(np.int64)*(4 if x.dtype==np.int16 else 1)
    if np.any(abs(x)>131072): raise ValueError('18-bit input required')
    h,wr,wi,fb=coefficients()
    y=((x<<4)-((np.concatenate(([0],x[:-1]))*8136950)<<4)//8388608)
    frames=[]
    rev=[int(f'{i:09b}'[::-1],2) for i in range(512)]
    for start in range(0,len(x)-319,160):
        a=[complex(0) for _ in range(512)]
        r=[0]*512; im=[0]*512
        for i in range(320): r[rev[i]]=int(y[start+i]*h[i]>>18)
        for stage in range(1,10):
            half=1<<(stage-1)
            for base in range(0,512,half*2):
                for j in range(half):
                    u=base+j; v=u+half; k=j*(512//(half*2))
                    tr=(r[v]*int(wr[k])-im[v]*int(wi[k]))>>22
                    ti=(r[v]*int(wi[k])+im[v]*int(wr[k]))>>22
                    r[u],r[v]=r[u]+tr,r[u]-tr
                    im[u],im[v]=im[u]+ti,im[u]-ti
                    assert all(-(1<<31)<=v<(1<<31) for v in (r[u],r[v],im[u],im[v])), "FFT overflow"
        p=[r[i]**2+im[i]**2 for i in range(257)]
        frames.append([sum(p[k]*int(fb[b,k]) for k in range(257))>>28 for b in range(64)])
    raw=np.asarray(frames,dtype=np.uint64).reshape(-1,64)
    return raw,raw.astype(np.float64).T*2.0**-42
def beamform_fixed(mic1, mic2, delay1=0, delay2=0, gain=16384):
    """DC filter, integer delays, Q15 common gain; discard first 256 outputs."""
    def signed(v,bits): return ((int(v)+(1<<(bits-1))) % (1<<bits))-(1<<(bits-1))
    history=[[0]*256 for _ in range(2)]; hp=[0,0]; previous=[0,0]; result=[]
    for n,pair in enumerate(zip(mic1,mic2)):
        delayed=[history[c][(n-d)%256] for c,d in enumerate((delay1,delay2))]
        result.append(signed((sum(delayed)*gain)>>15,18))
        for c,x in enumerate(pair):
            history[c][n%256]=signed(hp[c],18)
            hp[c]=signed(int(x)-previous[c]+((hp[c]*32604)>>15),32)
            previous[c]=int(x)
    return np.asarray(result)

if __name__=='__main__':
    h,r,i,f=coefficients()
    for name,arr,width in [('tw_re',r,24),('tw_im',i,24)]:
        (ROOT/(name+'.mem')).write_text(''.join(f'{int(v)&((1<<width)-1):0{(width+3)//4}x}\n' for v in arr))


    # Compact synchronous ROMs: symmetric Hann and at most two bands per bin.
    (ROOT/'hann_half.mem').write_text(''.join(f'{int(v):05x}\n' for v in h[:160]))
    sparse=[]
    for k in range(257):
        active=np.flatnonzero(f[:,k])
        first=int(active[0]) if len(active) else 0
        assert len(active)<=2
        assert len(active)<2 or active[1]==first+1
        lo=int(f[first,k]); hi=int(f[first+1,k]) if first<63 else 0
        sparse.append((first<<56)|(hi<<28)|lo)
    (ROOT/'mel_sparse.mem').write_text(''.join(f'{v:016x}\n' for v in sparse))
