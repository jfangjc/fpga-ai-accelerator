"""Run from repo root after building /tmp/libqnfrontend.so; Python oracle."""
import ctypes as C
import sys
import numpy as np
sys.path.insert(0, 'model')
from frontend import features

class State(C.Structure):
    _fields_ = [('count', C.c_double), ('mean', C.c_double*64), ('m2', C.c_double*64)]
lib = C.CDLL('/tmp/libqnfrontend.so')
fp = C.POINTER(C.c_float)
lib.qnf_features.argtypes = [fp, C.c_size_t, C.c_int, C.POINTER(State), fp]
lib.qnf_stream_new.argtypes = [C.POINTER(State)]
lib.qnf_stream_new.restype = C.c_void_p
lib.qnf_stream_push.argtypes = [C.c_void_p, fp, C.c_size_t, fp, C.c_uint]
lib.qnf_stream_flush.argtypes = [C.c_void_p, fp, C.c_uint]
lib.qnf_stream_free.argtypes = [C.c_void_p]
def ptr(x):
    return x.ctypes.data_as(fp)
for n in [257, 319, 320, 321, 416, 417, 4096, 160*101+3]:
    rng = np.random.default_rng(n)
    x = rng.normal(0, .1, n).astype('float32')
    T = (n//160+16)//16*16
    whole = np.zeros((64,T), 'float32')
    state = State()
    assert lib.qnf_features(ptr(x), n, 1, C.byref(state), ptr(whole)) == T
    ref = features(x, mode='running')
    assert np.max(np.abs(whole-ref)) < 1e-3
    for chunk in [1, 7, 160, 333, 4096, None]:
        s = lib.qnf_stream_new(None)
        assert s
        result = []
        pos = 0
        buf = np.empty((n//160+2,64), 'float32')
        while pos < n:
            k = min(n-pos, chunk or int(rng.choice([1,7,160,333,4096])))
            cap = 1 if chunk is None else len(buf)
            count = lib.qnf_stream_push(s, ptr(x[pos:pos+k]), k, ptr(buf), cap)
            assert count >= 0
            pos += k
            result.extend(buf[:count].copy())
            ready = max(0, (pos-256)//160+1)
            assert len(result) <= ready, (pos, len(result), ready)
            if chunk is not None:
                assert len(result) == ready
        while True:
            count = lib.qnf_stream_flush(s, ptr(buf), 1)
            assert count >= 0
            if not count:
                break
            result.extend(buf[:count].copy())
        out = np.array(result).T
        assert np.array_equal(out, whole[:,:n//160+1]), (n, chunk)
        assert lib.qnf_stream_push(s, ptr(x), 1, ptr(buf), 1) == -1
        lib.qnf_stream_free(s)
    print('PASS streaming start/context/flush/Python samples', n)
