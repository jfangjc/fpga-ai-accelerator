"""Compare full clips with independent golden logits for Python and C frontend inputs."""
import pathlib,json,subprocess
root=pathlib.Path('clips');rows=json.loads((root/'clips.json').read_text())
for r in rows[:3]:
 uid=r['id'];valid=round(r['seconds']*16000)//160+1;T=(valid+15)//16*16
 for source in ['python','c']:
  inp=root/(uid+('.utterance.i8' if source=='python' else '.utterance.c.f32.int8'))
  expected=(root/(uid+f'.{source}.golden')).read_bytes()
  out=subprocess.check_output(['./build/sw/qnrt/network_dump','quartznet_int8.bin',str(inp),str(T),str(valid)])
  assert out==expected,(uid,source)
  print('PASS full network',uid,source,'logit bytes',len(out),flush=True)
