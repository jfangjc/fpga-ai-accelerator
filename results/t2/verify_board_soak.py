"""Verify closed follow-up evidence and all original/new board final events."""
import csv
import json
from pathlib import Path
import sys

folder,original=map(Path,sys.argv[1:])
analysis=json.loads((folder/'analysis.json').read_text())
assert analysis['zero_loss_pass']
assert analysis['tcp_missing_events']==0
assert analysis['invalid_preview_latencies']==0
assert sum(1 for _ in csv.DictReader((folder/'soak.stages.csv').open()))==analysis['events']
before=json.loads(original.read_text())
after=[]
keys=('phrase','phrase_begin','capture','feature_count','final','word_ends')
for line in (folder/'soak.jsonl').open():
    e=json.loads(line)
    if e['state']=='Committed':
        after.append({k:e[k] for k in keys})
before=sorted(({k:e[k] for k in keys} for e in before),key=lambda e:e['phrase'])
after.sort(key=lambda e:e['phrase'])
assert before==after
if after and 'segments' in json.loads(next((folder/'soak.jsonl').open())):
    sys.path.insert(0,str(Path(__file__).parent/'ooo_emit'))
    from verify_caption import verify_caption
    verify_caption([json.loads(s) for s in (folder/'soak.jsonl').open()])
result=dict(finals_identical=len(after),events=analysis['events'],stage_rows=analysis['events'],
            zero_loss_pass=True,tcp_missing_events=0,final_fields=list(keys))
(folder/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
