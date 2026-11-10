"""Check every preview/stitch/final field and reconstruct every T1 caption."""
import hashlib
import json
from pathlib import Path
import sys
from verify_caption import verify_caption

folder,baseline=map(Path,sys.argv[1:])
root=Path(__file__).resolve().parents[4]
clips=(root/'model/testset_40.txt').read_text().split()
preview_fields=('phrase','capture','feature_count','window_start','emitted',
                'preview','tentative','word_ends','lost_frames')
final_fields=('phrase','phrase_begin','capture','feature_count','final','word_ends','emitted','lost_frames')
totals=dict(clips=len(clips),identical_finals=0,identical_previews=0,
            caption_events_verified=0,previews_before_earlier_final=0)
for clip in clips:
    read=lambda d:[json.loads(s) for s in (d/f'{clip}.pl.jsonl').open()]
    old,new=read(baseline),read(folder)
    for key,value in verify_caption(new).items(): totals[key]+=value
    for state,fields,counter in [('Speaking',preview_fields,'identical_previews'),
                                  ('Committed',final_fields,'identical_finals')]:
        select=lambda ev:sorted(({k:e[k] for k in fields} for e in ev
            if e['state']==state and not e.get('event_only')),key=lambda e:(e['phrase'],e['capture']))
        a,b=select(old),select(new)
        assert a==b,(clip,state)
        totals[counter]+=len(b)
totals.update(preview_fields=preview_fields,final_fields=final_fields)
totals['raw_files']={str(p.relative_to(folder)):dict(bytes=p.stat().st_size,
    sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(folder.glob('*'))
    if p.is_file() and p.suffix in ('.jsonl','.csv','.stdout','.stderr')}
(folder/'verification.json').write_text(json.dumps(totals,indent=2)+'\n')
print(json.dumps({k:v for k,v in totals.items() if k!='raw_files'},indent=2))
