"""Replay the published cached Python study, without new network inference.

Usage (repo root, WSL): python tests/t2/t2_prediction.py OUTPUT.json [T1_DIRECTORY]
"""
import argparse
import csv
import json
from pathlib import Path
import pickle
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'model'))
import stream_sim as sim

output=Path(sys.argv[1])
board=Path(sys.argv[2]) if len(sys.argv)>2 else None
study=json.loads((ROOT/'data/stream_core_sensitivity.json').read_text())
args=argparse.Namespace(cache=str(ROOT/'data/stream_cache_v1'),cap=15,
    digest=study['digest'],recompute=False,model=str(ROOT/'data/int8.pkl'))
layers=sim.load_layers(args.model)
full=sim.macs(layers,sim.window_frames(3,'silence'))
rows=[]
for clip in (ROOT/'model/testset_40.txt').read_text().split():
    base=Path(args.cache)/f'base-{clip}-cap15-{args.digest}.pkl'
    row=pickle.loads(base.read_bytes())['row']
    _,window=sim.cache_paths(args,row,3,'silence')
    assert window.exists(), 'This tool must never start new inference'
    cached=sim.load_clip(row,3,'silence',args)
    for phrase in cached['phrases']:
        phrase['final_macs']=sim.macs(layers,phrase['final_frames'])
    replay=sim.simulate_clip(cached,.48,round(.060*sim.SR),full)
    preview=' '.join(w for phrase in replay['previews'] for w,_ in phrase)
    record=dict(clip=clip,reference=row['text'].lower(),prediction_preview=preview,
        prediction_errors=sim.word_errors(row['text'].lower().split(),preview.split()),
        words=len(row['text'].split()),prediction_updates=len(replay['ages']))
    if board:
        events=[json.loads(s) for s in (board/f'{clip}.pl.jsonl').read_text().splitlines()]
        previews={e['phrase']:e['preview'] for e in events if e['state']=='Speaking' and not e.get('event_only')}
        actual=' '.join(previews[p] for p in sorted(previews))
        record.update(board_preview=actual,
            board_errors=sim.word_errors(record['reference'].split(),actual.split()),
            board_updates=sum(e['state']=='Speaking' and not e.get('event_only') for e in events))
        record['error_delta']=record['board_errors']-record['prediction_errors']
    rows.append(record)
result=dict(study_digest=study['digest'],compute_ms=60,window=3,lookahead=.48,padding='silence',
    words=sum(r['words'] for r in rows),prediction_errors=sum(r['prediction_errors'] for r in rows),
    prediction_updates=sum(r['prediction_updates'] for r in rows),per_clip=rows)
result['prediction_wer']=100*result['prediction_errors']/result['words']
if board:
    result.update(board_errors=sum(r['board_errors'] for r in rows),
        board_updates=sum(r['board_updates'] for r in rows),
        differing_preview_clips=sum(r['board_preview']!=r['prediction_preview'] for r in rows))
output.write_text(json.dumps(result,indent=2)+'\n', newline='\n')
print(json.dumps({k:v for k,v in result.items() if k!='per_clip'},indent=2))
