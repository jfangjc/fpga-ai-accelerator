"""Driver-level cosim regression against the real stt_engine Verilator top.

Run from the repository root after `make -C sw/engdrv cosim libqnrt.so`:
  test_cosim.py --misc                      program/protocol/recovery/timeout checks
  test_cosim.py --vectors K/N               manifest vectors with index % N == K
  test_cosim.py --network clip0:0:304:300   engdrv_run_network on a real window
  test_cosim.py --network-misc              RUN_NETWORK malformed/recovery/fallback
Network SPEC is CLIP:START:FRAMES:VALID (or CLIP:full) over that clip's saved
signed [64,T] C1 input features, or catA+B[+...] for the concatenated valid
frames of several clips (padded to 16; exercises >800-frame tiling).
--mode auto|layer|network selects engdrv_set_mode for --network runs. Logits are compared byte-for-byte, padding
included, with qnrt_run_frames and golden_int8.network (and with the stored
manifest logits for full clips). Results go to build/sw/engdrv/measurements/.
"""
import argparse, collections, csv, ctypes as C, json, os, pathlib, pickle, struct, sys
import numpy as np
ROOT=pathlib.Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/'model'))
from qnc2_export import export, read_qnw, run_layer
class Config(C.Structure): _fields_=[('cosim',C.c_int),('tx',C.c_uint),('rx',C.c_uint),('timeout',C.c_int)]
class Timing(C.Structure): _fields_=[(n,C.c_uint64) for n in ('submit','irq','ready','cycles')]+[(n,C.c_uint32) for n in ('status','starvation','terms')]
P=C.c_void_p; U=C.c_uint; BUILD=pathlib.Path(os.environ.get('BUILD_ROOT') or ROOT/'build')/'sw/engdrv'
lib=C.CDLL(str(BUILD/'libengdrv_cosim.so'),use_errno=True)
lib.engdrv_open.argtypes=[C.POINTER(P),C.POINTER(Config)]
lib.engdrv_load.argtypes=[P,C.c_char_p]
lib.engdrv_run_layer.argtypes=[P,U,P,P,U,U,C.c_int,P,C.c_size_t,C.POINTER(U),C.POINTER(U),C.POINTER(Timing)]
lib.engdrv_run_network.argtypes=[P,P,U,U,C.POINTER(P),C.POINTER(U),C.POINTER(U),C.POINTER(Timing)]
lib.engdrv_network_timing.argtypes=[P,U,C.POINTER(Timing)]
lib.engdrv_exchange.argtypes=[P,P,C.c_size_t,P,C.POINTER(C.c_size_t),C.POINTER(Timing)]
lib.engdrv_ping.argtypes=[P,C.POINTER(Timing)];lib.engdrv_close.argtypes=[P]
lib.engdrv_version.argtypes=[P,C.POINTER(U)]
class Info(C.Structure): _fields_=[('network',C.c_int),('commands',C.c_uint)]+[(n,C.c_uint64) for n in ('wall','exchange','outside','sync')]+[('sync_calls',C.c_uint)]
lib.engdrv_set_mode.argtypes=[P,C.c_int];lib.engdrv_network_capable.argtypes=[P];lib.engdrv_last_run.argtypes=[P,C.POINTER(Info)]
lib.engdrv_run_network.argtypes=[P,P,U,U,C.POINTER(P),C.POINTER(U),C.POINTER(U),C.POINTER(Timing)]
MODES={'auto':0,'layer':1,'network':2}
lib.engdrv_submit.argtypes=[P,P,U,U];lib.engdrv_poll.argtypes=[P,C.POINTER(P),C.POINTER(U),C.POINTER(U),C.POINTER(Timing),C.c_int]
ap=argparse.ArgumentParser();ap.add_argument('--misc',action='store_true');ap.add_argument('--vectors');ap.add_argument('--network',action='append',default=[])
ap.add_argument('--network-misc',action='store_true');ap.add_argument('--mode',choices=MODES,default='auto')
args=ap.parse_args()
def check(r):
 if r: raise RuntimeError(f'rc={r} errno={C.get_errno()}')
def buf(b):return C.create_string_buffer(b)
base=ROOT/'data/export';work=BUILD/'measurements';work.mkdir(parents=True,exist_ok=True)
manifest=json.loads((base/'vectors/manifest.json').read_text())
names={x['name']:x['id']-1 for x in json.loads((base/'layers.json').read_text())}
def kind_of(name,inputs):return 'add' if inputs>1 else 'depthwise' if '.dw' in name else 'pointwise'
# Each process exports its own program file so parallel shards never race.
tag=('misc' if args.misc else '')+('nmisc' if args.network_misc else '')+args.mode+(f"v{args.vectors.replace('/','of')}" if args.vectors else '')+''.join('_'+s.replace(':','-') for s in args.network)
program=work/f'program_{tag}.qnp2';program.write_bytes(export((base/'quartznet_int8.bin').read_bytes()))
e=P();timing=Timing();check(lib.engdrv_open(C.byref(e),C.byref(Config(1,2,3,3600000))));check(lib.engdrv_load(e,str(program).encode()))
if args.misc:
 check(lib.engdrv_ping(e,C.byref(timing)));print('PING cycles',timing.cycles,flush=True)
 version=U();check(lib.engdrv_version(e,C.byref(version)));assert version.value==0x10002 and lib.engdrv_network_capable(e)==1
 original=program.read_bytes()
 for offset,value in [(0,0),(4,2),(8,0),(12,16),(16,0),(20,0xfffffff0),(24,99),(28,99),(44,2048)]:
  damaged=bytearray(original);struct.pack_into('<I',damaged,offset,value);badpath=work/'bad.qnp2';badpath.write_bytes(damaged)
  assert lib.engdrv_load(e,str(badpath).encode())==-1
 badpath.unlink();check(lib.engdrv_ping(e,C.byref(timing)))
 print('PASS program validation 9 (prior program retained)',flush=True)
 # Malformed commands return engine status, then a valid command must succeed.
 for word,value,status in [(0,0,1),(1,2,1),(2,99,2),(4,16,3)]:
  h=[0x32434e51,1,0,77]+[0]*12;h[word]=value;cmd=buf(struct.pack('<16I',*h));response=C.create_string_buffer(64);n=C.c_size_t(64)
  check(lib.engdrv_exchange(e,cmd,64,response,C.byref(n),C.byref(timing)));assert timing.status==status;check(lib.engdrv_ping(e,C.byref(timing)))
 print('PASS malformed/recovery 4',flush=True)
 # Single-channel depthwise must not be confused with pointwise (groups=1).
 weights=bytearray(512)
 for k in range(3):weights[k*32]=1
 params=struct.pack('<i',0)+bytes(12)+struct.pack('<i',1<<30)+bytes(12)+struct.pack('<i',0)+bytes(12)
 record=struct.pack('<16I',0x32524c51,64+len(params)+len(weights),1,1,0,0,0,1,1,3,1,1,1,1,0,0)+params+weights
 synthetic=work/'single.qnp2';synthetic.write_bytes(struct.pack('<4I',0x32504e51,1,1,16+len(record))+record)
 check(lib.engdrv_load(e,str(synthetic).encode()));a=buf(bytes([2,4,6,100,100]));y=C.create_string_buffer(5);t=U();v=U()
 check(lib.engdrv_run_layer(e,0,a,None,5,3,1,y,5,C.byref(t),C.byref(v),C.byref(timing)))
 assert y.raw==bytes([3,6,5,3,0]) and (t.value,v.value)==(5,3)
 check(lib.engdrv_load(e,str(program).encode()));print('PASS single-channel depthwise geometry',flush=True)
 # Asynchronous API: a second submit is EBUSY, nonblocking poll, then valid logits.
 x=np.random.default_rng(47).integers(-20,21,(64,16),dtype=np.int8);out=P();t=U();v=U()
 check(lib.engdrv_submit(e,x.ctypes.data,16,13));assert lib.engdrv_submit(e,x.ctypes.data,16,13)==-1 and C.get_errno()==16
 while not (rc:=lib.engdrv_poll(e,C.byref(out),C.byref(t),C.byref(v),C.byref(timing),0)):pass
 assert rc==1 and (t.value,v.value)==(8,7);print('PASS async submit/EBUSY/poll',flush=True)
if args.vectors:
 k,n=map(int,args.vectors.split('/'));counts=collections.Counter();rows=[]
 for i,vector in enumerate(manifest):
  if i%n!=k:continue
  inputs=[buf((base/'vectors'/x['file']).read_bytes()) for x in vector['inputs']];inp=vector['inputs'][0];expected=(base/'vectors'/vector['output']['file']).read_bytes();output=C.create_string_buffer(len(expected));t=U();v=U()
  check(lib.engdrv_run_layer(e,names[vector['layer']],inputs[0],inputs[1] if len(inputs)>1 else None,inp['shape'][1],inp['valid_frames'],inp['dtype']=='int8',output,len(expected),C.byref(t),C.byref(v),C.byref(timing)))
  assert output.raw==expected,vector['layer'];assert [t.value,v.value]==[vector['output']['shape'][1],vector['output']['valid_frames']]
  kind=kind_of(vector['layer'],len(inputs));counts[kind]+=1
  rows.append([i,vector['clip'],vector['layer'],kind,inp['shape'][0],vector['output']['shape'][0],inp['shape'][1],t.value,len(expected),timing.cycles,timing.starvation,timing.terms])
  if sum(counts.values())%10==0:print(dict(counts),flush=True)
 with (work/f'vectors_{k}of{n}.csv').open('w',newline='') as f:csv.writer(f).writerows([['index','clip','layer','kind','ci','co','frames_in','frames_out','output_bytes','cycles','starvation','terms']]+rows)
 print('PASS vectors',args.vectors,dict(counts),'bytes',sum(r[8] for r in rows),flush=True)
def load_oracles():
 from golden_int8 import network
 q=C.CDLL(str(BUILD/'libqnrt.so'));q.qnrt_load.argtypes=[C.POINTER(P),C.c_char_p,U,U];q.qnrt_run_frames.argtypes=[P,P,U,U,C.POINTER(P),C.POINTER(U),C.POINTER(U)];q.qnrt_free.argtypes=[P]
 cpu=P();check(q.qnrt_load(C.byref(cpu),str(base/'quartznet_int8.bin').encode(),2048,1))
 with (ROOT/'data/int8.pkl').open('rb') as f:gold=pickle.load(f)
 layers=gold['layers'];first=layers[0]['name']
 clips={v['clip']:v for v in manifest if v['layer']==first}
 return network,q,cpu,layers,clips,list(clips)
def clip_features(clips,ids,k):
 inp=clips[ids[k]]['inputs'][0];C_,T=inp['shape']
 return np.frombuffer((base/'vectors'/inp['file']).read_bytes(),np.int8).reshape(C_,T),inp
def window(spec,clips,ids):
 parts=spec.split(':')
 if parts[0].startswith('cat'):
  pieces=[clip_features(clips,ids,int(k)) for k in parts[0][3:].split('+')]
  x=np.concatenate([f[:,:inp['valid_frames']] for f,inp in pieces],axis=1);valid=x.shape[1];frames=(valid+15)//16*16
  return np.ascontiguousarray(np.pad(x,((0,0),(0,frames-valid)))),frames,valid,0,None
 k=int(parts[0].removeprefix('clip'));feats,inp=clip_features(clips,ids,k)
 if parts[1]=='full':start,frames,valid=0,feats.shape[1],inp['valid_frames']
 else:start,frames,valid=map(int,parts[1:])
 assert start+frames<=feats.shape[1] and valid<=frames
 return np.ascontiguousarray(feats[:,start:start+frames]),frames,valid,start,ids[k] if parts[1]=='full' else None
def run_window(x,frames,valid):
 a=P();t=U();v=U()
 check(lib.engdrv_submit(e,x.ctypes.data,frames,valid))
 while not (rc:=lib.engdrv_poll(e,C.byref(a),C.byref(t),C.byref(v),C.byref(timing),60000)):pass
 assert rc==1,C.get_errno()
 return C.string_at(a,29*t.value),t.value,v.value
if args.network:
 network,q,cpu,layers,clips,clip_ids=load_oracles();last=layers[-1]['name']
 check(lib.engdrv_set_mode(e,MODES[args.mode]))
 for spec in args.network:
  x,frames,valid,start,full=window(spec,clips,clip_ids)
  out,tout,vout=run_window(x,frames,valid)
  b=P();ct=U();cv_=U();xf=x.T.copy();check(q.qnrt_run_frames(cpu,xf.ctypes.data,frames,valid,C.byref(b),C.byref(ct),C.byref(cv_)))
  assert (tout,vout)==(ct.value,cv_.value) and out==C.string_at(b,29*tout),'engdrv != qnrt'
  tensors,lengths=network(x,layers,capture=True,length=valid);assert out==tensors[last].tobytes() and vout==lengths[last],'engdrv != golden_int8'
  oracles='qnrt_run_frames+golden_int8.network'
  if full:
   stored=next(m for m in manifest if m['clip']==full and m['layer']==last)
   assert out==(base/'vectors'/stored['output']['file']).read_bytes();oracles+='+manifest'
  info=Info();check(lib.engdrv_last_run(e,C.byref(info)))
  rows=[];lt=Timing();mode='network' if info.network else 'layer'
  for i,l in enumerate(layers[:1] if info.network else layers):
   check(lib.engdrv_network_timing(e,i,C.byref(lt)));rows.append([i,'RUN_NETWORK' if info.network else l['name'],'network' if info.network else kind_of(l['name'],len(l['inputs'])),lt.cycles,lt.starvation,lt.terms,lt.irq-lt.submit])
  total=sum(r[3] for r in rows);assert total==timing.cycles
  by=collections.Counter()
  for r in rows:by[r[2]]+=r[3]
  name=f"network_{mode}_{spec.replace(':','_').replace('+','-')}.csv"
  with (work/name).open('w',newline='') as f:csv.writer(f).writerows([['layer_index','layer','kind','cycles','starvation','terms','sim_submit_irq_ns']]+rows)
  print('PASS network',mode,spec,f'start={start} T_in={frames} valid={valid} -> T_out={tout} valid={vout}',f'{29*tout} logit bytes == {oracles}',
        'commands',info.commands,'cycles',total,dict(by),'starvation',timing.starvation,'outside_ms',round(info.outside/1e6,3),'sim_wall_s',round((timing.ready-timing.submit)/1e9,1),'->',name,flush=True)
 q.qnrt_free(cpu)
if args.network_misc:
 from qnc2_export import run_network
 network,q,cpu,layers,clips,clip_ids=load_oracles()
 assert lib.engdrv_network_capable(e)==1
 x,frames,valid,_,_=window('clip2:full',clips,clip_ids);prog=program.read_bytes()
 def raw(packet,cap):
  r=C.create_string_buffer(cap);n=C.c_size_t(cap);check(lib.engdrv_exchange(e,buf(packet),len(packet),r,C.byref(n),C.byref(timing)));return timing.status,n.value
 good=run_network(prog,x.tobytes(),frames,valid,tag=901)
 expect=network(x,layers,capture=True,length=valid)[0][layers[-1]['name']].tobytes()
 st,n=raw(good,64+29*16);assert st==0 and n==64+(29*16+15)//16*16,(st,n)
 print('PASS raw RUN_NETWORK envelope (model.qnc2_export.run_network) status 0',flush=True)
 cases=[]
 h=bytearray(good);struct.pack_into('<I',h,24,641);cases.append(('allocated T_in=641 > limit',bytes(h),4))
 h=bytearray(good);struct.pack_into('<I',h,64+64*frames+16+12,99);cases.append(('record 1 op=99',bytes(h),4))
 h=bytearray(good);struct.pack_into('<I',h,40,185);cases.append(('record count 185',bytes(h),4))
 h=bytearray(good[:64+64*frames+16+4096]);cases.append(('truncated program, early TLAST',bytes(h),3))
 for name,packet,status in cases:
  st,n=raw(packet,64+29*400);assert st==status and n==64,(name,st,n,status)
  out,tout,vout=run_window(x,frames,valid);info=Info();check(lib.engdrv_last_run(e,C.byref(info)))
  assert out==expect and info.network==1,name
  print(f'PASS malformed RUN_NETWORK ({name}) -> status {st}, next engdrv_run_network bit-exact',flush=True)
 # Host deadline aborts a RUN_NETWORK mid-stream; reset; network works again.
 e2=P();check(lib.engdrv_open(C.byref(e2),C.byref(Config(1,2,3,1))));check(lib.engdrv_load(e2,str(program).encode()));check(lib.engdrv_set_mode(e2,2))
 a=P();t=U();v=U();x6,f6,v6,_,_=window('clip0:0:640:640',clips,clip_ids)
 assert lib.engdrv_run_network(e2,x6.ctypes.data,f6,v6,C.byref(a),C.byref(t),C.byref(v),C.byref(timing))==-1 and C.get_errno()==110
 check(lib.engdrv_ping(e2,C.byref(timing)));lib.engdrv_close(e2)
 out,tout,vout=run_window(x,frames,valid);assert out==expect
 print('PASS RUN_NETWORK host timeout -> ETIMEDOUT, reset, PING, next network bit-exact',flush=True)
 # Forced network rejects a window beyond the limit (E2BIG) without touching
 # the engine; LAYER forces 186 RUN_LAYER commands; AUTO uses one RUN_NETWORK.
 x7,f7,v7,_,_=window('clip0:0:656:650',clips,clip_ids);check(lib.engdrv_set_mode(e,2))
 assert lib.engdrv_run_network(e,x7.ctypes.data,f7,v7,C.byref(a),C.byref(t),C.byref(v),C.byref(timing))==-1 and C.get_errno()==7
 check(lib.engdrv_set_mode(e,1));out,tout,vout=run_window(x,frames,valid);info=Info();check(lib.engdrv_last_run(e,C.byref(info)))
 assert out==expect and info.network==0 and info.commands==186
 check(lib.engdrv_set_mode(e,0));out,tout,vout=run_window(x,frames,valid);check(lib.engdrv_last_run(e,C.byref(info)))
 assert out==expect and info.network==1 and info.commands==1
 print('PASS mode: NETWORK T_in=656 -> E2BIG; LAYER -> 186 RUN_LAYER bit-exact; AUTO -> 1 RUN_NETWORK bit-exact',flush=True)
 q.qnrt_free(cpu)
lib.engdrv_close(e)
if args.misc:
 # A host deadline aborts a long legal command, resets engine, then PING works.
 e=P();check(lib.engdrv_open(C.byref(e),C.byref(Config(1,2,3,1))));packet=run_layer(read_qnw((base/'quartznet_int8.bin').read_bytes())[0],bytes(64*784),784,774);partial=buf(packet);response=C.create_string_buffer(64+64*392);n=C.c_size_t(len(response))
 assert lib.engdrv_exchange(e,partial,len(packet),response,C.byref(n),C.byref(timing))==-1 and C.get_errno()==110
 check(lib.engdrv_ping(e,C.byref(timing)));lib.engdrv_close(e);print('PASS timeout/reset/recovery',flush=True)
program.unlink()
