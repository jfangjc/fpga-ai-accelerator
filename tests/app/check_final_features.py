"""Measure replay phrase-final feature differences against Python (no inference)."""
import ast
import ctypes as c
import json
from pathlib import Path
import subprocess
import sys
import numpy as np
import soundfile as sf
import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'model'))
import stream_policy as policy
from frontend import features
torch.set_num_threads(1)
tree = ast.parse((ROOT/'model/stream_sim.py').read_text())
functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)
             and n.name in ('block_active', 'phrases')]
ns = dict(np=np, policy=policy, SR=16000, BLOCK=160)
exec(compile(ast.Module(body=functions, type_ignores=[]), 'stream_sim.py', 'exec'), ns)
subprocess.run(['cc', '-shared', '-fPIC', '-O2', '-ffp-contract=off',
    str(ROOT/'sw/frontend_c/frontend.c'), '-lm', '-o', '/tmp/libcap-final.so'], check=True)
lib = c.CDLL('/tmp/libcap-final.so')
ptr = c.POINTER(c.c_float)
lib.qnf_features.argtypes = [ptr, c.c_size_t, c.c_int, c.c_void_p, ptr]
maximum = square = count = phrases = 0
boundary_max = 0
for clip in (ROOT/'model/testset_40.txt').read_text().split():
    x, rate = sf.read(next((ROOT/'data/LibriSpeech').rglob(clip+'.flac')), dtype='int16')
    assert rate == 16000
    x = np.concatenate((x.astype(np.float32)/32768, np.zeros(12800, np.float32)))
    for begin, _, end in ns['phrases'](x):
        audio = np.ascontiguousarray(x[begin:end])
        expected = features(audio)
        actual = np.zeros_like(expected)
        assert lib.qnf_features(audio.ctypes.data_as(ptr), len(audio), 0, None,
                                actual.ctypes.data_as(ptr)) == actual.shape[1]
        valid = len(audio)//160+1
        delta = actual[:,:valid].astype(np.float64)-expected[:,:valid]
        maximum = max(maximum, float(np.abs(delta).max()))
        boundary_max = max(boundary_max, float(np.abs(delta[:,[0,1,-2,-1]]).max()))
        square += float((delta*delta).sum())
        count += delta.size
        phrases += 1
print(json.dumps(dict(phrases=phrases, values=count, max_abs=maximum,
    boundary_max_abs=boundary_max, rms=(square/count)**.5)))
