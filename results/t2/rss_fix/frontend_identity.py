import ctypes as C, numpy as np, sys
fp=C.POINTER(C.c_float)
class St(C.Structure): _fields_=[('count',C.c_double),('mean',C.c_double*64),('m2',C.c_double*64)]
libs=[C.CDLL(p) for p in sys.argv[1:3]]
for l in libs: l.qnf_features.argtypes=[fp,C.c_size_t,C.c_int,C.c_void_p,fp]; l.qnf_features.restype=C.c_int
new=libs[1]; new.qnf_features_ws.argtypes=[fp,C.c_size_t,C.c_int,C.c_void_p,fp,C.c_void_p]; new.qnf_features_ws.restype=C.c_int
new.qnf_features_scratch.restype=C.c_size_t; new.qnf_features_scratch.argtypes=[C.c_size_t]
scratch=np.zeros(1536*1024+64,'uint8'); base=scratch.ctypes.data; base=(base+63)//64*64
for n in [257,321,4803,48000,160000,255840,255999]:
  for run in (0,1):
    x=np.random.default_rng(n).normal(0,.1,n).astype('float32'); T=(n//160+16)//16*16
    outs=[]
    for f in ('old','new','ws'):
      y=np.full((64,T),7,'float32'); s=St(); s.count=5
      for c in range(64): s.mean[c]=-10+c*.01; s.m2[c]=4+c
      lib=libs[0] if f=='old' else new
      args=(x.ctypes.data_as(fp),n,run,C.byref(s),y.ctypes.data_as(fp))
      r=lib.qnf_features_ws(*args,C.c_void_p(base)) if f=='ws' else lib.qnf_features(*args)
      outs.append((r,y.tobytes(),bytes(s)))
    assert outs[0]==outs[1]==outs[2],(n,run)
    assert new.qnf_features_scratch(n)<=1536*1024
    print('IDENTICAL n=%d run=%d T=%d scratch=%d'%(n,run,outs[0][0],new.qnf_features_scratch(n)))
