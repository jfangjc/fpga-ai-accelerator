"""Compare C CTC to the actual pure-Python stream_sim function on recorded logits.

Also checks overlapping windows including repeated IDs at stitch boundaries.
No torch/model import is needed: extract the pure function with ast.
"""
import ast
import ctypes as c
import random
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
tree = ast.parse((ROOT/'model/stream_sim.py').read_text())
fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ('ctc_text','ctc_words')]
namespace = {'BLANK': 28, 'VOCAB': " abcdefghijklmnopqrstuvwxyz'", 'OUT_HOP':320, 'SR':16000}
exec(compile(ast.Module(body=fns, type_ignores=[]), 'stream_sim.py', 'exec'), namespace)
ctc = namespace['ctc_text']
subprocess.run(['cc', '-shared', '-fPIC', '-O2', str(HERE/'stream.c'), '-o', '/tmp/libcap-stream.so'], check=True)
lib = c.CDLL('/tmp/libcap-stream.so')


def decode(ids):
    array = (c.c_uint8*len(ids))(*ids)
    out = c.create_string_buffer(4096)
    lib.cap_ctc(array, c.c_size_t(len(ids)), out, c.c_size_t(len(out)))
    return out.value.decode()


for path in sorted((ROOT/'data/export/clips').glob('network_*.out')):
    raw = path.read_bytes()
    assert len(raw)%29==0
    t = len(raw)//29
    signed = [v if v<128 else v-256 for v in raw]
    ids = [max(range(29), key=lambda b: signed[b*t+j]) for j in range(t)]
    assert decode(ids)==ctc(ids), path
    print('PASS recorded logits', path.name, t)


class Stitch(c.Structure):
    _fields_ = [('ids', c.c_uint8*2048), ('frames', c.c_uint*2048), ('count', c.c_uint),
                ('emitted', c.c_uint), ('lost', c.c_uint)]


rng = random.Random(42)
for trial in range(100):
    state = Stitch()
    committed, committed_frames, emitted = [], [], 0
    for end in range(12, 700, 12):
        start = max(0, end-150)
        ids = [rng.randrange(29) for _ in range(end-start)]
        stop = max(0, len(ids)-24)
        first = max(0, emitted-start)
        if first<stop:
            committed += ids[first:stop]
            committed_frames += list(range(start+first,start+stop))
            emitted = start+stop
        tail = max(stop, first)
        a, b = c.create_string_buffer(4096), c.create_string_buffer(4096)
        array = (c.c_uint8*len(ids))(*ids)
        assert lib.cap_stitch(c.byref(state),array,len(ids),start,24,a,b,len(a))==0
        assert a.value.decode()==ctc(committed)
        assert b.value.decode()==ctc(committed+ids[tail:])
        assert state.emitted==emitted
        ends = (c.c_uint*2048)()
        nwords = lib.cap_word_ends(state.ids,state.frames,state.count,ends)
        expected = namespace['ctc_words'](committed,committed_frames,0)
        assert nwords==len(expected)
        assert list(ends[:nwords])==[round(t/.02) for _,t in expected]
print('PASS 5800 overlapping-window comparisons against stream_sim CTC/stitch contract')
