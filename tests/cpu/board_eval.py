"""Run from ~/stt/cpu on board. Results are ignored measurement data."""
import subprocess,json,re,statistics,time,pathlib,sys
root=pathlib.Path('.')
def run(args):
 p=subprocess.run(args,text=True,capture_output=True,check=True)
 return p.stdout,p.stderr
# Record conditions before measurement; refuse to compete with the IRQ soak.
conditions=run(['sh','-c','date -u; uname -a; uptime; cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor; cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq; ps -eo pid,pcpu,comm --sort=-pcpu | head -12'])[0]
print(conditions,flush=True)
shared='--shared-correctness' in sys.argv
recover='--recover-idle' in sys.argv
resume='--resume-final' in sys.argv
if not shared and not recover and subprocess.run(['pgrep','-x','irq_soak'],capture_output=True).returncode==0:
 raise SystemExit('IRQ soak active: defer official benchmark')
results=json.loads((root/'results.json').read_text())['clips'] if resume else []
for nt in ([] if shared or recover else [1,2,4]):
 out,err=run(['./build/sw/qnrt/qnrt_test','quartznet_int8.bin','vectors.tsv',str(nt),'bench'])
 print(out,flush=True)
 (root/f'validation_{nt}.log').write_text(out+err, newline='\n')
for mode in ([] if resume else ['utterance','running'] if shared else ['utterance']):
 for r in json.loads((root/'clips/clips.json').read_text()):
  uid=r['id'];cmd=['./build/sw/qnrt/qnrt_transcribe','quartznet_int8.bin',f'clips/{uid}.wav','--mode',mode,'--prior','running_prior.json',* ([] if shared else ['--bench'])]
  if uid in ['1089-134691-0023','1188-133604-0025','121-127105-0003']:cmd+=['--features',f'clips/{uid}.{mode}.c.f32']
  out,err=run(cmd);times=[dict((k,float(v)) for k,v in re.findall(r'(\w+)=([\d.]+)',line)) for line in err.splitlines()]
  hyp=out.strip();ref=r['text'].split();hs=hyp.split();prev=list(range(len(hs)+1))
  for i,w in enumerate(ref,1):
   row=[i]
   for j,h in enumerate(hs,1):row.append(min(row[-1]+1,prev[j]+1,prev[j-1]+(w!=h)))
   prev=row
  results.append(dict(irq_active=subprocess.run(['pgrep','-x','irq_soak'],capture_output=True).returncode==0,id=uid,mode=mode,hyp=hyp,errors=prev[-1],words=len(ref),seconds=r['seconds'],timings=times,network_median=statistics.median(t['network'] for t in (times[1:] if len(times)>1 else times))))
  print(mode,uid,results[-1]['network_median'],hyp,flush=True)
  (root/'results.json').write_text(json.dumps(dict(conditions=conditions,shared_correctness=shared,clips=results),indent=2), newline='\n')

# Repeat contaminated clips after initial work; no idle polling or sleeps.
if recover:
 for old in list(results):
  if not old['irq_active']:continue
  if subprocess.run(['pgrep','-x','irq_soak'],capture_output=True).returncode==0:
   raise SystemExit('IRQ soak still active after complete evaluation; retain diagnostic results')
  uid=old['id'];cmd=['./build/sw/qnrt/qnrt_transcribe','quartznet_int8.bin',f'clips/{uid}.wav','--mode',old['mode'],'--prior','running_prior.json','--bench']
  out,err=run(cmd)
  assert out.strip()==old['hyp']
  times=[dict((k,float(v)) for k,v in re.findall(r'(\w+)=([\d.]+)',line)) for line in err.splitlines()]
  old['timings']=times;old['network_median']=statistics.median(t['network'] for t in times[1:]);old['irq_active']=False
  print('idle repeat',old['mode'],uid,old['network_median'],flush=True)
  (root/'results.json').write_text(json.dumps(dict(conditions=conditions,shared_correctness=shared,clips=results),indent=2), newline='\n')
if recover:
 conditions+=run(['sh','-c','date -u; uptime; pgrep -a irq_soak || true; cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null || true; cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null || true'])[0]
 for nt in [1,2,4]:
  out,err=run(['./build/sw/qnrt/qnrt_test','quartznet_int8.bin','vectors.tsv',str(nt),'bench'])
  (root/f'validation_{nt}.log').write_text(out+err, newline='\n')
  print(out,flush=True)
 out,err=run(['./build/sw/qnrt/qnrt_test','quartznet_int8.bin','vectors.tsv','4','bench','profile'])
 (root/'profile_idle.log').write_text(out+err, newline='\n')
 (root/'results.json').write_text(json.dumps(dict(conditions=conditions,shared_correctness=False,clips=results),indent=2), newline='\n')

if recover:
 out,err=run(['./build/sw/qnrt/peak'])
 (root/'peak_idle.log').write_text(out+err, newline='\n')

