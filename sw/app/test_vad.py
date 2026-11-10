"""Held-out waveform VAD/phrase check against the streaming study policy.

No fitting: uses PCM16 replay quantisation and the fixed dev-selected 0.01 RMS.
"""
import ast
import ctypes as c
import json
import subprocess
import sys
from pathlib import Path
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'model'))
import stream_policy as policy
tree = ast.parse((ROOT/'model/stream_sim.py').read_text())
functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)
             and n.name in ('block_active', 'phrases')]
ns = dict(np=np, policy=policy, SR=16000, BLOCK=160)
exec(compile(ast.Module(body=functions, type_ignores=[]), 'stream_sim.py', 'exec'), ns)
subprocess.run(['cc', '-shared', '-fPIC', '-O2', str(ROOT/'sw/app/stream.c'),
                '-o', '/tmp/libcap-vad.so'], check=True)
lib = c.CDLL('/tmp/libcap-vad.so')
class Vad(c.Structure):
    _fields_ = [('state', c.c_int), ('begin', c.c_size_t), ('onset', c.c_size_t),
                ('boundary', c.c_size_t), ('silent', c.c_uint)]
lib.cap_vad_step.argtypes = [c.POINTER(Vad), c.c_int, c.c_size_t, c.c_size_t]
blocks = boundaries = 0
for clip in (ROOT/'model/testset_40.txt').read_text().split():
    path = next((ROOT/'data/LibriSpeech').rglob(clip+'.flac'))
    x, rate = sf.read(path, dtype='int16')
    assert rate == 16000
    x = np.concatenate((x.astype(np.float32)/32768, np.zeros(12800, np.float32)))
    state, actual = Vad(), []
    for pos in range(0, len(x), 160):
        end = min(pos+160, len(x))
        energy = sum(float(v)*float(v) for v in x[pos:end])/(end-pos)
        active = energy >= policy.VAD_RMS**2
        assert active == ns['block_active'](x, pos)
        change = lib.cap_vad_step(c.byref(state), active, pos, end)
        if change == -1:
            actual.append((state.begin, state.onset, end))
        blocks += 1
    if state.state == 1:
        actual.append((state.begin, state.onset, len(x)))
    expected = list(ns['phrases'](x))
    assert actual == expected, (clip, actual, expected)
    boundaries += len(actual)
print(json.dumps(dict(clips=40, blocks=blocks, frame_disagreements=0,
                      phrases=boundaries, phrase_disagreements=0, fitted=False)))
