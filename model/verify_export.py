"""Validate QNW1 packing against calibration cache and layer-vector files."""
import json,pathlib,pickle,struct
import numpy as np
from golden_int8 import layer,requant

def verify(cache='data/int8.pkl',root='data/export'):
    root=pathlib.Path(root); blob=(root/'quartznet_int8.bin').read_bytes()
    with open(cache,'rb') as f: q=pickle.load(f)
    magic,version,hs,count,ds,table,total,reserved,scale=struct.unpack_from('<4s7If',blob)
    assert (magic,version,hs,count,ds,table,total,reserved)==(b'QNW1',1,64,len(q['layers']),128,64,len(blob),0)
    for i,l in enumerate(q['layers']):
        d=struct.unpack_from('<32I',blob,64+i*128)
        for offset,size in zip(d[12:20:2],d[13:20:2]):
            assert offset%64==0 and offset>=64+128*count and offset+size<=len(blob)
        if l['op']=='conv':
            o,c,k=l['w'].shape; op=(o+31)//32; kp=(c*k+15)//16
            w=np.frombuffer(blob,dtype=np.int8,count=d[13],offset=d[12]).reshape(op,kp,16,32)
            w=w.transpose(0,3,1,2).reshape(op*32,kp*16)
            np.testing.assert_array_equal(w[:o,:c*k].reshape(o,c,k),l['w'])
            assert not np.any(w[o:]) and not np.any(w[:,c*k:])
            for slot,key in [(14,'b'),(16,'mult'),(18,'shift')]:
                np.testing.assert_array_equal(np.frombuffer(blob,dtype='<i4',count=o,offset=d[slot]),l[key])
    manifest=root/'vectors/manifest.json'
    if manifest.exists():
        lookup={l['name']:l for l in q['layers']}
        def read(m): return np.fromfile(root/'vectors'/m['file'],dtype=m['dtype']).reshape(m['shape'])
        for v in json.loads(manifest.read_text()):
            l=lookup[v['layer']]; ins=[read(m) for m in v['inputs']]
            if l['op']=='conv': actual=layer(ins[0],l)
            else:
                a=requant(ins[0].astype(np.int32),l['mult0'],l['shift0'],-32768,32767)
                b=requant(ins[1].astype(np.int32),l['mult1'],l['shift1'],-32768,32767)
                actual=np.clip(a+b,0,255).astype(np.uint8)
            np.testing.assert_array_equal(actual,read(v['output']))
        print('all layer vectors: PASS;',len(json.loads(manifest.read_text())),'vectors')
    print('blob bounds, tiled packing, parameter arrays: PASS;',len(blob),'bytes;',count,'layers; input scale',scale)

if __name__=='__main__': verify()
