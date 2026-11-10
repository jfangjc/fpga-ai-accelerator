import numpy as np, sys
for n in sys.argv[1:]:
    p=np.fromfile(n,'<u8').reshape(-1,64).astype(float)*2.0**-42
    L=10*np.log10(p+1e-12)
    tot=L.mean(1)
    # 0.5 Hz gating modulation per band
    t=np.arange(len(L))/100
    ref=np.exp(-2j*np.pi*0.5*t)
    mod=np.abs(((L-L.mean(0))*ref[:,None]).mean(0))*2
    b=int(mod.argmax())
    print(f"{n}: mean dB={tot.mean():6.1f} std(frame mean)={tot.std():5.2f} max band mod={mod.max():5.2f} dB at band {b}; per-band mean dB lo/mid/hi = {L[:,:8].mean():.1f}/{L[:,24:40].mean():.1f}/{L[:,56:].mean():.1f}")
