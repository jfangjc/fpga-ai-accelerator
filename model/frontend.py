"""Evaluation frontend for NGC QuartzNet 1.0.0rc1 (NeMo v1.0.0).

mel_power returns unpadded frames; features pads normalized output to 16.
Streaming callers must retain preemphasis history and wait for centered frames.
"""
import numpy as np
import torch
import librosa

PARAMS = dict(sample_rate=16000, preemph=0.97, n_fft=512, win_length=320,
              hop_length=160, window='hann', periodic=False, n_mels=64,
              mel_scale='slaney', mel_norm='slaney', fmin=0, fmax=8000,
              power=2, log_guard=2**-24, normalize='per_feature',
              center=True, pad_mode='reflect', dither=0, pad_to=16,
              std_ddof=1, std_epsilon=1e-5)

def mel_power(x):
    x = np.asarray(x, dtype=np.float32)
    if x.ndim != 1 or len(x) <= 256 or not np.isfinite(x).all():
        raise ValueError('Expected finite mono audio with >256 samples')
    y = torch.from_numpy(x.copy())
    y = torch.cat((y[:1], y[1:] - .97*y[:-1]))
    z = torch.stft(y, 512, 160, 320, torch.hann_window(320, periodic=False),
                   center=True, pad_mode='reflect', return_complex=True)
    fb = librosa.filters.mel(sr=16000, n_fft=512, n_mels=64, fmin=0,
                             fmax=8000, htk=False, norm='slaney')
    return (torch.from_numpy(fb) @ z.abs().square()).numpy()

def log_normalize(mel, mode='utterance', state=None):
    """Causal Welford statistics; optional prior {count, mean, m2}.

    Running updates include the current frame and carry across calls. A prior
    may be estimated ONLY on dev-clean; no test statistics are permitted.
    """
    z = np.log(np.asarray(mel, np.float32) + np.float32(2**-24))
    if mode == 'utterance':
        if z.shape[1] < 2: raise ValueError('Need two frames for sample std')
        return ((z-z.mean(1, keepdims=True))/(z.std(1, ddof=1, keepdims=True)+1e-5)).astype(np.float32), None
    if mode != 'running': raise ValueError(mode)
    s = dict(count=0, mean=np.zeros(64), m2=np.zeros(64)) if state is None else {
        k: np.array(v, copy=True) if k != 'count' else int(v) for k,v in state.items()}
    out = np.empty_like(z)
    for t in range(z.shape[1]):
        s['count'] += 1
        delta = z[:,t]-s['mean']; s['mean'] += delta/s['count']
        s['m2'] += delta*(z[:,t]-s['mean'])
        std = np.sqrt(s['m2']/max(1,s['count']-1))
        out[:,t] = (z[:,t]-s['mean'])/(std+1e-5)
    return out, s

def features(x, mode='utterance', state=None):
    f, _ = log_normalize(mel_power(x), mode, state)
    return np.pad(f, ((0,0),(0,(-f.shape[1])%16)))
