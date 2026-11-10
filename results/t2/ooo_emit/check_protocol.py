"""Validate the shipped TCP client's rendering of the measured late snapshot."""
import json
from pathlib import Path
import sys
root=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(root/'tools'))
from caption_client import caption_text

folder=Path(sys.argv[1])
late=json.loads((folder/'late-join.json').read_text())
e=late['event']
assert e['snapshot'] and late['latency_ms']<50
assert caption_text(e)==e['caption']
pending=[s for s in e['segments'] if s['state']=='Finalising']
assert pending
human=caption_text(e,annotate_pending=True)
assert all(f"[{s['phrase']} pending: {s['text']}]" in human for s in pending)
result=dict(snapshot_self_contained=True,human_pending_markers=True,
            snapshot_latency_ms=late['latency_ms'])
(folder/'client-verification.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
