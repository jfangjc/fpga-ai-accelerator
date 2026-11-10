"""Summarize board outputs copied into ignored data/cpu (no invented numbers)."""
import json,pathlib,numpy as np
root=pathlib.Path('data/cpu')
r=json.loads((root/'results.json').read_text())
print('shared board:',r.get('shared_correctness'))
for mode in ['utterance','running']:
 rows=[x for x in r['clips'] if x['mode']==mode]
 if not rows:continue
 times=np.array([np.median([t['network']+t['frontend'] for t in (x['timings'][1:] if len(x['timings'])>1 else x['timings'])]) for x in rows]);seconds=sum(x['seconds'] for x in rows)
 print(mode,'clips',len(rows),'WER',100*sum(x['errors'] for x in rows)/sum(x['words'] for x in rows),'latencies',np.percentile(times,[50,90,100]),'RTF',sum(times)/seconds,'RSS KiB',max(t['rss_kib'] for x in rows for t in x['timings']))
for p in root.glob('*.c.f32'):
 ref=np.fromfile(str(p).replace('.c.f32','.f32'),np.float32);got=np.fromfile(p,np.float32)
 a=np.fromfile(str(p)+'.int8',np.int8);b=np.fromfile(str(p).replace('.c.f32','.i8'),np.int8)
 print(p.name,'max abs',np.max(np.abs(ref-got)),'INT8 mismatch',np.mean(a!=b))
