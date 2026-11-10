"""Supplement the unchanged strict analyzer with residency and comparison evidence.

python report.py NEW_SOAK ORIGINAL_SOAK
Both directories contain closed raw logs. No board/MMIO access.
"""
import json
from pathlib import Path
import re
import statistics
import sys

folder,original=map(Path,sys.argv[1:])
read=lambda p:[json.loads(s) for s in p.read_text().splitlines()]
new=json.loads((folder/'analysis.json').read_text())
old=json.loads((original/'analysis.json').read_text())
monitor=read(folder/'monitor.jsonl')
active=[r for r in monitor if r['seconds']>=150 and r['frames']<new['expected_frames']]
def slope(rows):
    x=[r['seconds']/60 for r in rows]; y=[r['rss_kib'] for r in rows]
    a,b=statistics.mean(x),statistics.mean(y)
    return sum((u-a)*(v-b) for u,v in zip(x,y))/sum((u-a)**2 for u in x)
def stats(v):
    v=sorted(v)
    def q(p):
        x=(len(v)-1)*p; i=int(x)
        return v[i]+(v[min(i+1,len(v)-1)]-v[i])*(x-i)
    return dict(n=len(v),p50=q(.5),p95=q(.95),max=max(v))
transitions=[]
for r in active:
    if not transitions or transitions[-1]['rss_kib']!=r['rss_kib']:
        transitions.append(dict(seconds=r['seconds'],rss_kib=r['rss_kib']))
late=[r for r in active if r['seconds']>=600]
plateau=[r for r in active if r['seconds']>=transitions[-1]['seconds']]
result=dict(strict_t2_pass=new['strict_t2_pass'],
    rss_active_capture_after_150s=dict(samples=len(active),first_kib=active[0]['rss_kib'],
        last_kib=active[-1]['rss_kib'],min_kib=min(r['rss_kib'] for r in active),
        max_kib=max(r['rss_kib'] for r in active),slope_kib_per_minute=slope(active),
        transitions=transitions),
    rss_active_capture_after_600s=dict(min_kib=min(r['rss_kib'] for r in late),
        max_kib=max(r['rss_kib'] for r in late),slope_kib_per_minute=slope(late)),
    rss_last_active_plateau=dict(start_seconds=plateau[0]['seconds'],
        end_seconds=plateau[-1]['seconds'],samples=len(plateau),
        min_kib=min(r['rss_kib'] for r in plateau),max_kib=max(r['rss_kib'] for r in plateau),
        slope_kib_per_minute=slope(plateau)),
    # The primary analyzer retains teardown and its exact endpoint predicate.
    primary_rss_including_drain=new['rss_after_150s'])
assert json.loads((folder/'playlist.json').read_text())==json.loads((original/'playlist.json').read_text())
result['identical_playlist']=True
speech={r['phrase']:r['speech_end_sample'] for r in json.loads((folder/'final-latency.json').read_text())['rows']}
ev=read(original/'soak.jsonl')
lat=[]
for e in ev:
    if e['state']=='Committed':
        mapped=e['audio_time']+(speech[e['phrase']]-e['oldest_sample'])/e['sample_rate']
        lat.append((e['time']-mapped)*1000)
result['original_speech_end_to_final_display_ms']=stats(lat)
result['new_speech_end_to_final_display_ms']=new['speech_end_to_final_display_ms']
(folder/'comparison.json').write_text(json.dumps(result,indent=2)+'\n')
lines=['| Audio min | Preview p50 / p95 / max, ms | Merges | RSS median / max, KiB | Power mean / max, W | PS CPU mean, % of one core |',
       '|---|---:|---:|---:|---:|---:|']
for b in new['buckets']:
    t=b['latency_ms']
    lines.append(f"| {b['minutes']} | {t['p50']:.3f} / {t['p95']:.3f} / {t['max']:.3f} | {b['merged']} | {b['rss_median_kib']:.0f} / {b['rss_max_kib']} | {b['power_mean_w']:.3f} / {b['power_max_w']:.3f} | {b['ps_cpu_mean_percent_one_core']:.3f} |")
(folder/'table.md').write_text('\n'.join(lines)+'\n')
# Self-contained, theme-aware HTML for the inline comparison chart.
def points(data):
    return ' '.join(f'{65+i*112},{240-b["latency_ms"]["p95"]*.2:.2f}' for i,b in enumerate(data))
labels=''.join(f'<text x="{65+i*112}" y="270" text-anchor="middle">{5*i}-{5*i+5}</text>' for i in range(6))
grid=''.join(f'<line x1="50" x2="655" y1="{240-v*.2}" y2="{240-v*.2}"/><text x="42" y="{244-v*.2}" text-anchor="end">{v}</text>' for v in (0,250,500,750,1000))
html=f'''<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;color:var(--foreground);font-family:var(--font-sans)}}
h3{{font-size:16px;margin:0 0 8px}} p{{font-size:13px;color:var(--muted-foreground);margin:6px 0}}
svg{{width:100%;height:290px;font-size:12px;overflow:visible}}text{{fill:var(--muted-foreground)}}
.grid line{{stroke:var(--border)}}.old{{stroke:var(--chart-1)}}.new{{stroke:var(--chart-2)}}
polyline{{fill:none;stroke-width:3;stroke-linejoin:round}}
.oldkey{{color:var(--chart-1)}}.newkey{{color:var(--chart-2)}}
</style></head><body><h3>Preview display p95 across the 30-minute soak</h3>
<p><span class="oldkey">● Original serial PL finals</span> &nbsp; <span class="newkey">● Independent CPU long finals</span></p>
<svg viewBox="0 0 700 290" role="img" aria-label="Five-minute preview display latency comparison, milliseconds"><g class="grid">{grid}</g>
<line x1="50" x2="655" y1="190" y2="190" stroke="var(--muted-foreground)" stroke-dasharray="4 4"/>
<polyline class="old" points="{points(old['buckets'])}"/><polyline class="new" points="{points(new['buckets'])}"/>
{labels}<text x="353" y="289" text-anchor="middle">Audio minutes · dashed line: 250 ms stretch target</text></svg>
<p>Merges: {old['merged']} → {new['merged']}. Independent inference preserves service cadence; ordered display still waits for earlier finals.</p>
</body></html>'''
(folder/'comparison.html').write_text(html)
print(json.dumps(result,indent=2))
