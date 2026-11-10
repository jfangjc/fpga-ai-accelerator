"""Tables and final scheduling diagnostics; accepts a closed board run only.

python report.py NEW_SOAK PL_BASELINE ORDERED_AUTO_BASELINE
"""
import json
from pathlib import Path
import sys

new,pl,ordered=map(Path,sys.argv[1:4])
read=lambda p:json.loads(p.read_text())
analyses=[read(p/'analysis.json') for p in (pl,ordered,new)]
def triple(s):
    return ' / '.join(f'{s[k]:.3f}' for k in ('p50','p95','max'))
def stats(values):
    v=sorted(values)
    if not v: return dict(n=0)
    def q(p):
        x=(len(v)-1)*p;i=int(x)
        return v[i]+(v[min(i+1,len(v)-1)]-v[i])*(x-i)
    return dict(n=len(v),p50=q(.5),p95=q(.95),max=max(v))

rows=['| Audio min | Preview p50 / p95 / max, ms | Merges | RSS median / max, KiB | Power mean / max, W | PS CPU mean, % of one core |',
      '|---|---:|---:|---:|---:|---:|']
for b in analyses[-1]['buckets']:
    rows.append(f"| {b['minutes']} | {triple(b['latency_ms'])} | {b['merged']} | "
                f"{b['rss_median_kib']:.0f} / {b['rss_max_kib']} | "
                f"{b['power_mean_w']:.3f} / {b['power_max_w']:.3f} | "
                f"{b['ps_cpu_mean_percent_one_core']:.3f} |")
(new/'table.md').write_text('\n'.join(rows)+'\n')
rows=['| Configuration | Preview p50 / p95 / max, ms | Final latency p50 / p95 / max, ms | Merges | strict_t2_pass |',
      '|---|---:|---:|---:|---|']
for name,a in zip(('PL serial finals','Ordered auto / 3 CPU threads','Phrase-indexed auto / 3 CPU threads'),analyses):
    # The original final-latency diagnostic was added retrospectively.
    final=a.get('speech_end_to_final_display_ms')
    if final is None:
        final=read(ordered/'comparison.json')['original_speech_end_to_final_display_ms']
    rows.append(f"| {name} | {triple(a['latency_ms'])} | {triple(final)} | {a['merged']} | {str(a['strict_t2_pass']).lower()} |")
(new/'comparison.md').write_text('\n'.join(rows)+'\n')
print((new/'comparison.md').read_text())
print((new/'table.md').read_text())
if '--tables-only' in sys.argv: raise SystemExit(0)

diagnostics={}
for name,folder in zip(('pl','ordered_auto','ooo_auto'),(pl,ordered,new)):
    finals=[]
    with (folder/'soak.jsonl').open() as stream:
        for line in stream:
            e=json.loads(line)
            if e['state']=='Committed': finals.append(e)
    grouped={}
    for label,selected in [('all',finals),('short_le640',[e for e in finals if e['feature_count']<=640]),
                           ('long_gt640',[e for e in finals if e['feature_count']>640])]:
        grouped[label]={key:stats([fn(e) for e in selected]) for key,fn in (
            ('compute_ms',lambda e:e['compute_ms']),
            ('queue_ms',lambda e:e.get('queue_ms',0)),
            ('ready_to_emit_ms',lambda e:e.get('ordered_wait_ms',0)),
            ('boundary_to_display_ms',lambda e:(e['time']-e['audio_time']-
                (e['capture']*16000-e['oldest_sample'])/e['sample_rate'])*1000))}
    diagnostics[name]=grouped
(new/'final-scheduling.json').write_text(json.dumps(diagnostics,indent=2)+'\n')
