"""Verify routing evidence without requiring intentional backend names to match."""
import csv
import hashlib
import json
from pathlib import Path
import sys

folder,original=map(Path,sys.argv[1:3])
analysis=json.loads((folder/'analysis.json').read_text())
assert analysis['zero_loss_pass']
assert analysis['tcp_missing_events']==0
assert analysis['invalid_preview_latencies']==0
events=[json.loads(s) for s in (folder/'soak.jsonl').read_text().splitlines()]
stages=list(csv.DictReader((folder/'soak.stages.csv').open()))
assert len(stages)==len(events)
before=json.loads(original.read_text())
after=[e for e in events if e['state']=='Committed']
keys=('phrase','phrase_begin','capture','feature_count','final','word_ends')
assert len(before)==len(after)
assert all(all(a[k]==b[k] for k in keys) for a,b in zip(before,after))
assert [e['phrase'] for e in after]==sorted(e['phrase'] for e in after)
for phrase in {e['phrase'] for e in events}:
    updates=[e for e in events if e['phrase']==phrase and not e.get('event_only')]
    assert [e['capture'] for e in updates]==sorted(e['capture'] for e in updates)
    assert updates[-1]['state']=='Committed'
    assert all(e['state']=='Speaking' for e in updates[:-1])
# Compare exact TCP payload sequence (the connection snapshot can replace the
# initial Listening row); all actual caption updates must be present in order.
tcp=[r['event'] for r in map(json.loads,(folder/'tcp.jsonl').read_text().splitlines())]
assert [e for e in tcp if not e.get('event_only')]==[e for e in events if not e.get('event_only')]
dump=folder/'dump'
hashes={p.name:dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
        for p in sorted(dump.glob('final-*'))}
result=dict(finals_identical=len(after),matching_final_fields=keys,
            intentional_changes=['backend','commands'],events=len(events),stage_rows=len(stages),
            ordered_caption_tcp_payloads=True,ordered_phrase_updates=True,
            final_dumps=hashes)
if len(sys.argv)>3:
    with Path(sys.argv[3]).open() as stream:
        old_finals=[e for e in map(json.loads,stream) if e['state']=='Committed']
    extra=('emitted','lost_frames','window_start','preview','tentative','caption')
    assert len(old_finals)==len(after)
    assert all(all(a[k]==b[k] for k in extra) for a,b in zip(old_finals,after))
    result['additional_original_raw_final_fields']=extra
(folder/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='final_dumps'},indent=2))
