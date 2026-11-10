"""Compare host before/after outputs and, optionally, exact final dumps."""
import csv
import hashlib
import json
from pathlib import Path
import sys

folder=Path(sys.argv[1])
def read(name):
    return [json.loads(s) for s in (folder/name).read_text().splitlines()]
before, after=read('baseline.jsonl'), read('events.jsonl')
fields=('phrase','state','event_only','phrase_begin','word_ends','capture',
        'feature_count','window_start','emitted','preview','tentative','final','lost_frames','caption')
assert len(before)==len(after)
assert all(all(a.get(k)==b.get(k) for k in fields) for a,b in zip(before,after))
stages=list(csv.DictReader((folder/'stages.csv').open()))
assert len(stages)==len(after)
assert all(float(s['stdout'])>=float(s['tcp']) for s in stages)
result=dict(events=len(after),stage_rows=len(stages),matching_fields=list(fields),
            final_texts=[e['final'] for e in after if e['state']=='Committed'])
if (folder/'before-dump').exists() and (folder/'after-dump').exists():
    names=sorted(p.name for p in (folder/'before-dump').glob('final-*'))
    assert names
    assert names==sorted(p.name for p in (folder/'after-dump').glob('final-*'))
    hashes={}
    for name in names:
        a=(folder/'before-dump'/name).read_bytes()
        b=(folder/'after-dump'/name).read_bytes()
        assert a==b,name
        hashes[name]=dict(bytes=len(a),sha256=hashlib.sha256(a).hexdigest())
    result['identical_final_bytes']=hashes
text=json.dumps(result,indent=2)+'\n'
(folder/'verification.json').write_text(text)
print(text)
