"""Run independent full-top commands in bounded parallel simulator processes.

Each worker has its own Verilated top and protocol tests. Contiguous shards
preserve the malformed-command/recovery pair. No golden data is recomputed.
"""
import concurrent.futures,csv,os,struct,subprocess,tempfile
from pathlib import Path

def run(mode):
    source=Path('depthwise150.bin' if mode=='dw150' else 'depthwise.bin')
    data=source.read_bytes();count,=struct.unpack_from('<I',data);offset=4;cases=[]
    for _ in range(count):
        nl,nc,ny,*_=struct.unpack_from('<6I',data,offset)
        end=offset+24+nl+nc+ny;cases.append(data[offset:end]);offset=end
    assert offset==len(data)
    workers=min(int(os.environ.get('DW_SIM_WORKERS','8')),count)
    assert workers>=1
    executable=Path('obj_engine/Vstt_engine').resolve()
    csvname='depthwise150_cycles.csv' if mode=='dw150' else 'depthwise_cycles.csv'
    def worker(index):
        chunk=cases[count*index//workers:count*(index+1)//workers]
        with tempfile.TemporaryDirectory(prefix='dw_shard_',dir='.') as directory:
            directory=Path(directory)
            (directory/source.name).write_bytes(struct.pack('<I',len(chunk))+b''.join(chunk))
            result=subprocess.run([str(executable),mode],cwd=directory,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            if result.returncode:
                raise RuntimeError(result.stdout)
            rows=list(csv.DictReader((directory/csvname).open()))
            assert len(rows)==len(chunk)
            print(f'PASS {mode} shard {index+1}/{workers}: {len(rows)} commands',flush=True)
            return result.stdout,rows
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        results=list(pool.map(worker,range(workers)))
    Path(mode+'.log').write_text(''.join(log for log,_ in results), newline='\n')
    with open(csvname,'w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['name','cycles']);writer.writeheader()
        for _,rows in results:writer.writerows(rows)
    print(f'PASS depthwise {mode}: {count} cases, {workers} independent full-top simulators')

if __name__=='__main__':
    run('dw');run('dw150')

