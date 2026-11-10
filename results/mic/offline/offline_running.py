import json, sys, numpy as np
sys.path.insert(0, 'hw/measurements/mic/offline')
from offline_eval import *
p = np.fromfile(sys.argv[1], '<u8').reshape(-1, 64).T.astype(np.float64)*2.0**-42
ref = ' '.join(c['text'] for c in json.load(open(sys.argv[2]))).split()
prior = json.load(open('data/export/running_prior.json')); model = QuartzNet()
for g in (0, 10, 15, 20, 25, 30):
    for b0 in (False, True):
        er, n = run_running(p, model, ref, prior, 8, g, b0)
        print(f'running gain={g:2d}dB band0fix={b0!s:5}: WER {er}/{n} = {100*er/n:.2f}%', flush=True)
