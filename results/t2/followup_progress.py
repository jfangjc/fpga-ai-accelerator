"""Read-only partial-soak summary; keeps only numeric samples in memory."""
import collections
import json
from pathlib import Path
import statistics
import sys

folder = Path(sys.argv[1])
buckets = collections.defaultdict(list)
merges = 0
for line in (folder/'soak.jsonl').open():
    try:
        e = json.loads(line)
    except json.JSONDecodeError:
        continue
    if not e.get('event_only'):
        merges = max(merges,e['merged'])
        if e['state']=='Speaking' and e['latency_valid']:
            buckets[int(e['capture']//300)].append(e['update_ms'])
rss = collections.defaultdict(list)
warm=[]
for line in (folder/'monitor.jsonl').open():
    r = json.loads(line)
    rss[int(r['seconds']//300)].append(r['rss_kib'])
    if r['seconds']>=150:
        warm.append((r['seconds']/60,r['rss_kib']))
def q(v,p):
    v=sorted(v); x=(len(v)-1)*p; i=int(x)
    return v[i]+(v[min(i+1,len(v)-1)]-v[i])*(x-i)
xm=statistics.mean(x for x,y in warm); ym=statistics.mean(y for x,y in warm)
slope=sum((x-xm)*(y-ym) for x,y in warm)/sum((x-xm)**2 for x,y in warm)
print(json.dumps(dict(merged=merges,rss_slope_kib_per_minute=slope,buckets=[dict(minutes=f'{i*5}-{(i+1)*5}',n=len(v),
    p50=q(v,.5),p95=q(v,.95),max=max(v),rss_min_kib=min(rss[i]),
    rss_median_kib=statistics.median(rss[i]),rss_max_kib=max(rss[i])) for i,v in sorted(buckets.items())]),indent=2))
