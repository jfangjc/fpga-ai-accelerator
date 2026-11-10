"""Full-network stimuli with independent integer oracle and stored short logits."""
import argparse,json,struct,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'model'))
from qnc2_export import run_network,read_program
from golden_int8 import network

def layers(records):
    result=[]
    for i,(d,a) in enumerate(records,1):
        op,src,src1,relu,co,ci,k,stride,dilation,pad,groups=d[:11]
        row=dict(name=str(i),inputs=[str(src)],op='add' if op==2 else 'conv')
        if op==2:
            row['inputs'].append(str(src1))
            for key,value in zip(('mult0','shift0','mult1','shift1'),a):row[key]=np.frombuffer(value,'<i4')
        else:
            nk=(ci*k+15)//16
            row['w']=np.frombuffer(a[0],np.int8).reshape(-1,nk,16,32).transpose(0,3,1,2).reshape(-1,nk*16)[:co,:ci*k].reshape(co,ci,k)
            for key,value in zip(('b','mult','shift'),a[1:]):row[key]=np.frombuffer(value,'<i4')
            row.update(stride=stride,padding=pad,dilation=dilation,groups=groups,relu=bool(relu))
        row['inputs']=['input' if n=='0' else n for n in row['inputs']]
        result.append(row)
    return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--all',action='store_true');args=parser.parse_args()
    directory=ROOT/'data/export/vectors';manifest=json.loads((directory/'manifest.json').read_text())
    first=next(e for e in manifest if e['inputs'][0]['shape']==[64,32]);entries=[e for e in manifest if e['clip']==first['clip']]
    x=np.frombuffer((directory/first['inputs'][0]['file']).read_bytes(),np.int8).reshape(64,32)
    program=(ROOT/'data/export/quartznet_qnc2.bin').read_bytes();graph=layers(read_program(program))
    cases=[]
    for t,v in ([(32,18),(48,48),(112,102),(300,300),(304,300),(320,320),(150,150),(640,640)] if args.all else [(32,18)]):
        features=np.resize(x,(64,t)).copy()
        tensors,lengths=network(features,graph,capture=True,length=v)
        logits=tensors['186'];valid=lengths['186']
        if t==32:
            stored=(directory/entries[-1]['output']['file']).read_bytes()
            assert logits.tobytes()==stored,'network oracle differs from stored manifest'
        cmd=run_network(program,features.tobytes(),t,v)
        cases.append((f'network/T{t}/valid{v}',cmd,logits.tobytes(),0,logits.shape[1],valid))
    if args.all:
        name,cmd,y,status,t,v=cases[0]
        malformed=bytearray(cmd)
        struct.pack_into('<I',malformed,64+64*32+16+16,999)
        cases.extend([('malformed',bytes(malformed),b'',4,0,0),
                      ('recovery/T32/valid18',cmd,y,0,t,v),
                      ('truncated',cmd[:512],b'',3,0,0),
                      ('recovery2/T32/valid18',cmd,y,0,t,v)])
    with Path('network.bin').open('wb') as f:  # read by tb_engine in the working directory
        f.write(struct.pack('<I',len(cases)))
        for name,cmd,y,status,t,v in cases:
            label=name.encode();f.write(struct.pack('<6I',len(label),len(cmd),len(y),status,t,v));f.write(label);f.write(cmd);f.write(y)
    print(f'Generated {len(cases)} whole-network cases; T32 oracle matches stored logits')
if __name__=='__main__':main()

