"""Compare every final field and retain raw evidence hashes for the T1 batch."""
import hashlib
import json
from pathlib import Path
import sys

folder,baseline=map(Path,sys.argv[1:])
root=Path(__file__).resolve().parents[4]
clips=(root/'model/testset_40.txt').read_text().split()
fields=('phrase','phrase_begin','capture','feature_count','final','word_ends','emitted','lost_frames')
finals=0
for clip in clips:
    read=lambda d:[e for e in map(json.loads,(d/f'{clip}.pl.jsonl').read_text().splitlines())
                   if e['state']=='Committed']
    before,after=(sorted(read(d),key=lambda e:e['phrase']) for d in (baseline,folder))
    assert len(before)==len(after),clip
    assert all(all(a[k]==b[k] for k in fields) for a,b in zip(before,after)),clip
    finals+=len(after)
raw=sorted(p for p in folder.rglob('*') if p.is_file() and (
    p.suffix in ('.jsonl','.stdout','.stderr','.i8','.logits','.tsv') or p.name.endswith('.stages.csv')))
hashes={str(p.relative_to(folder)):dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in raw}
result=dict(clips=len(clips),identical_finals=finals,matching_fields=fields,raw_files=hashes)
(folder/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='raw_files'},indent=2))
