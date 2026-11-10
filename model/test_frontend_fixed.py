"""Numerical checks; optional speech paths: python -m model.test_frontend_fixed *.flac."""
import sys
import numpy as np
from .frontend_fixed import coefficients,mel_power_fixed

def float_stream(x):
    h,_,_,_=coefficients()
    # Independent Slaney filters from librosa when the model environment is ready.
    try:
        import librosa
        fb=librosa.filters.mel(sr=16000,n_fft=512,n_mels=64,norm='slaney')
    except ImportError:
        fb=coefficients()[3]/2**28
    y=np.r_[x[0],x[1:]-.97*x[:-1]]
    return np.stack([fb@abs(np.fft.rfft(y[s:s+320]*np.hanning(320),512))**2 for s in range(0,len(x)-319,160)],axis=1)

def check(x):
    raw,f=mel_power_fixed(x)
    # Centered model frame 1 has its Hann support at streaming samples 0..319.
    from .frontend import mel_power
    ref=mel_power(x.astype(np.float32)/32768)[:,1:1+len(raw)]
    d=10*np.log10((f+2**-24)/(ref+2**-24))
    print(len(raw),'frames; dB max',np.max(abs(d)),'RMS',np.sqrt(np.mean(d*d)))
    assert np.max(abs(d))<=.25
    assert np.sqrt(np.mean(d*d))<=.02
    return d
if __name__=='__main__':
    if len(sys.argv)>1:
        import soundfile as sf
        for path in sys.argv[1:]:
            x,sr=sf.read(path,dtype='int16');assert sr==16000;print(path);check(x)
    else:
        r=np.random.default_rng(3)
        for amplitude in (32767,1000,4):check(r.integers(-amplitude,amplitude+1,16000,dtype=np.int16))



def check_full_scale():
    # Include the negative endpoint, Nyquist, steps, DC, and full-scale noise.
    rng=np.random.default_rng(127)
    patterns=[np.full(640,131071),np.full(640,-131072),
              np.tile([-131072,131071],320),
              np.r_[np.full(320,-131072),np.full(320,131071)],
              rng.integers(-131072,131072,640)]
    for x in patterns: mel_power_fixed(x)
    print('PASS full-scale overflow: 5 patterns / 15 frames')

if __name__=='__main__':
    check_full_scale()
    if len(sys.argv)>1:
        x,sr=sf.read(sys.argv[1],dtype='int16')
        print('Quiet speech (1/100 amplitude)');check((x.astype(np.int32)//100).astype(np.int16))
