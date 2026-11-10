"""Offline mic tuning: PL mel frames recorded by `fetool mic -o` during PC playback.
Simulates the mic VAD (band 1..63 energy vs min-statistics floor) and whole-phrase
finals with software gain and optional band-0 substitution; float QuartzNet."""
import json, sys
import numpy as np
sys.path.insert(0, 'model')
from quartznet_float import QuartzNet, greedy

def ed(a, b):
    d = list(range(len(b)+1))
    for i, x in enumerate(a, 1):
        p, d[0] = d[0], i
        for j, y in enumerate(b, 1): p, d[j] = d[j], min(d[j]+1, d[j-1]+1, p+(x != y))
    return d[-1]

def vad(e, snr_db, abs_min=2e-6, window=500, block=50):
    floor_hist = []; state = 0; silent = 0; begin = 0; boundary = 0; phrases = []; blockmin = np.inf
    mins = []
    for t, x in enumerate(e):
        blockmin = min(blockmin, x)
        if (t+1) % block == 0: mins.append(blockmin); mins = mins[-window//block:]; blockmin = np.inf
        floor = min(mins + [blockmin])
        active = x >= max(abs_min, floor*10**(snr_db/10))
        if not state and active:
            begin = max(t-20, boundary); state = 1; silent = 0
        if state:
            silent = 0 if active else silent+1
            if silent >= 70 or t+1-begin >= 1500:
                phrases.append((begin, t+1)); boundary = t+1; state = 0
    if state: phrases.append((begin, len(e)))
    return phrases

def run(p, model, ref, prior_mean, snr=8, gain_db=0, band0=False, verbose=False):
    e = p[1:].sum(0)
    hyp = []
    for b, en in vad(e, snr):
        z = np.log((p[:, b:en]*10**(gain_db/10)).astype(np.float32) + np.float32(2**-24))
        if band0: z[0] = z[1] + (prior_mean[0]-prior_mean[1])
        z = (z-z.mean(1, keepdims=True))/(z.std(1, ddof=1, keepdims=True)+1e-5)
        T = z.shape[1]; z = np.pad(z, ((0, 0), (0, (-T) % 16)))
        txt = greedy(model(z, length=T))
        if verbose: print(f'  [{b/100:6.2f}-{en/100:6.2f}] {txt}')
        hyp += txt.split()
    return ed(ref, hyp), len(ref)

if __name__ == '__main__':
    frames, refjson = sys.argv[1:3]
    p = np.fromfile(frames, '<u8').reshape(-1, 64).T.astype(np.float64)*2.0**-42
    ref = ' '.join(c['text'] for c in json.load(open(refjson))).split()
    prior = json.load(open('data/export/running_prior.json'))
    model = QuartzNet()
    print('phrases', len(vad(p[1:].sum(0), 8)))
    for snr in (6, 8, 10):
        for g in (0, 10, 20, 30):
            for b0 in (False, True):
                er, n = run(p, model, ref, prior['mean'], snr, g, b0)
                print(f'snr={snr:2d} gain={g:2d}dB band0fix={b0!s:5}: WER {er}/{n} = {100*er/n:.2f}%', flush=True)
    run(p, model, ref, prior['mean'], 8, 20, True, verbose=True)

def run_running(p, model, ref, prior, snr=8, gain_db=0, band0=False):
    """Proxy for previews: causal running normalisation from the dev prior."""
    sys.path.insert(0, 'model'); from frontend import log_normalize
    hyp = []
    for b, en in vad(p[1:].sum(0), snr):
        mel = (p[:, b:en]*10**(gain_db/10)).astype(np.float32)
        if band0:
            z1 = np.log(mel[1]+np.float32(2**-24)); mel[0] = np.exp(z1+(prior['mean'][0]-prior['mean'][1]))
        z, _ = log_normalize(mel, 'running', prior)
        T = z.shape[1]; z = np.pad(z, ((0, 0), (0, (-T) % 16)))
        hyp += greedy(model(z, length=T)).split()
    return ed(ref, hyp), len(ref)
