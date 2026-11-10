"""Low-priority read-only progress; does not access hardware registers."""
import json
from pathlib import Path
import statistics
import sys

base=Path('/home/ubuntu/stt/t2/final_routing')
folder=base/'soak'
if sys.argv[1:]==['--light']:
    monitor=[json.loads(s) for s in (folder/'monitor.jsonl').read_text().splitlines()]
    warm=[r['rss_kib'] for r in monitor if r['seconds']>=150]
    last=monitor[-1]
    merges=sum(s.startswith('merge_event ') for s in (folder/'soak.stderr').read_text().splitlines())
    print(json.dumps(dict(seconds=round(last['seconds'],1),frames=last['frames'],merged=merges,
        rss_kib=last['rss_kib'],warm_range_kib=max(warm)-min(warm) if warm else None)))
    raise SystemExit
def read(path):
    rows=[]
    with path.open() as stream:
        for line in stream:
            try:
                e=json.loads(line)
                # Captions grow through the run. Keep only scalar diagnostics,
                # never an in-memory copy of the accumulated caption history.
                rows.append({k:v for k,v in e.items() if k in (
                    'seconds','frames','rss_kib','status','state','event_only',
                    'merged','update_ms','capture','latency_valid')})
            except json.JSONDecodeError: pass  # writer can be in its last line
    return rows
monitor=read(folder/'monitor.jsonl')
events=read(folder/'soak.jsonl')
warm=[r for r in monitor if r['seconds']>=150]
result=dict(seconds=monitor[-1]['seconds'],frames=monitor[-1]['frames'],
            previews=sum(e['state']=='Speaking' and not e.get('event_only') for e in events),
            finals=sum(e['state']=='Committed' for e in events),
            merged=max(e.get('merged',0) for e in events),
            fifo_overflow_samples=sum(bool(r['status']&2) for r in monitor))
def stats(values):
    values=sorted(values)
    def q(f):
        x=(len(values)-1)*f; i=int(x)
        return round(values[i]+(values[min(i+1,len(values)-1)]-values[i])*(x-i),3)
    return dict(n=len(values),p50=q(.5),p95=q(.95),max=values[-1]) if values else {}
previews=[e for e in events if e['state']=='Speaking' and not e.get('event_only')]
result['preview_ms']=stats([e['update_ms'] for e in previews if e['latency_valid']])
result['completed_buckets']=[dict(minutes=f'{5*i}-{5*i+5}',**stats([
    e['update_ms'] for e in previews if i*300<=e['capture']<(i+1)*300 and e['latency_valid']]))
    for i in range(min(6,int((monitor[-1]['frames']/100-1)//300)))]
if warm:
    xs=[r['seconds']/60 for r in warm]; ys=[r['rss_kib'] for r in warm]
    xm,ym=statistics.mean(xs),statistics.mean(ys)
    result['rss_warm_kib']=dict(first=ys[0],last=ys[-1],min=min(ys),max=max(ys))
    if len(xs)>1:
        result['rss_slope_kib_per_min']=sum((x-xm)*(y-ym) for x,y in zip(xs,ys))/sum((x-xm)**2 for x in xs)
print(json.dumps(result))
