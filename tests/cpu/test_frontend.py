"""Host frontend boundary and quantization regression; needs NumPy/Python reference."""
import ctypes as C, pathlib,sys,numpy as np,torch
sys.path.insert(0,'model')
from frontend import features
from golden_int8 import round_away
torch.set_num_threads(4)
lib=C.CDLL('/tmp/libqnfrontend.so')
fp=C.POINTER(C.c_float);ip=C.POINTER(C.c_int8)
lib.qnf_features.argtypes=[fp,C.c_size_t,C.c_int,C.c_void_p,fp];lib.qnf_features.restype=C.c_int
lib.qnf_quantize.argtypes=[fp,C.c_size_t,C.c_float,ip]
for n in [257,319,320,321,160*17+3]:
 x=np.random.default_rng(n).normal(0,.1,n).astype('float32');T=(n//160+16)//16*16;y=np.zeros((64,T),'float32')
 assert lib.qnf_features(x.ctypes.data_as(fp),n,0,None,y.ctypes.data_as(fp))==T
 ref=features(x);err=np.max(np.abs(y-ref));assert err<1e-3,(n,err)
 print('PASS frontend samples',n,'max abs',err)
v=np.arange(-260,260,dtype='float32')*.5
v=np.concatenate([v,np.nextafter(v,-np.inf),np.nextafter(v,np.inf)]).astype('float32');q=np.empty(v.size,'int8')
lib.qnf_quantize(v.ctypes.data_as(fp),v.size,C.c_float(1),q.ctypes.data_as(ip))
assert np.array_equal(q,np.clip(round_away(v),-128,127).astype('int8'))
print('PASS',len(v),'quantization ties and saturation boundaries')
