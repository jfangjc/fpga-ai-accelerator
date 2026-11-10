"""Analyse an acoustic microphone acceptance run (tests/mic/mic_acceptance.ps1).

    python tests/mic/mic_analyze.py results/mic/TAG
Reads playlist.json, playback.json, pc-tcp.tsv (PC receipt time + event) and
the board session (session.jsonl, stderr.txt). Writes summary.json.
"""
import gzip
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np


def ed(a, b):
    d = list(range(len(b)+1))
    for i, x in enumerate(a, 1):
        p, d[0] = d[0], i
        for j, y in enumerate(b, 1):
            p, d[j] = d[j], min(d[j]+1, d[j-1]+1, p+(x != y))
    return d[-1]


def utc(s):
    s = s.rstrip('Z')
    if '.' in s:
        head, frac = s.split('.', 1)
        frac = re.match(r'\d+', frac).group(0)[:6]
        s = f'{head}.{frac}'
    return datetime.fromisoformat(s).timestamp()


def pct(x):
    x = np.asarray(x, float)
    return None if not len(x) else dict(p50=round(float(np.percentile(x, 50)), 1),
                                         p95=round(float(np.percentile(x, 95)), 1),
                                         max=round(float(x.max()), 1), n=len(x))


d = Path(sys.argv[1])


def lines(name):  # evidence logs may be stored gzip-compressed
    p = d/name
    return gzip.open(f'{p}.gz', 'rt', encoding='utf-8') if not p.exists() else open(p, encoding='utf-8')


playlist = json.load(open(d/'playlist.json'))
clips = playlist['clips']
play0 = utc(json.load(open(d/'playback.json'))['play_start_utc'])
tcp = []
for line in lines('pc-tcp.tsv'):
    t, e = line.rstrip('\n').split('\t', 1)
    tcp.append((utc(t)-play0, json.loads(e)))
board = [json.loads(l) for l in lines('session.jsonl')]
stderr = (d/'stderr.txt').read_text()

finals = {}
last_preview = {}
for t, e in tcp:
    if e.get('event_only') or e.get('snapshot'):
        continue
    if e.get('state') == 'Committed':
        finals.setdefault(e['phrase'], (t, e['final']))
    elif e.get('state') == 'Speaking':
        seg = [s for s in e.get('segments', []) if s['phrase'] == e['phrase']]
        if seg:
            last_preview[e['phrase']] = seg[0].get('tentative') or seg[0]['text']  # full preview text
order = sorted(finals)
ref = ' '.join(c['text'] for c in clips).split()
hyp = ' '.join(finals[p][1] for p in order).split()
phyp = ' '.join(last_preview.get(p, '') for p in order).split()
final_errors, preview_errors = ed(ref, hyp), ed(ref, phyp)

# Per clip: finals received between this clip's start and the next clip's start.
per_clip, latency = [], []
for k, c in enumerate(clips):
    lo = c['start']+0.3
    hi = clips[k+1]['start']+0.3 if k+1 < len(clips) else float('inf')
    mine = [finals[p] for p in order if lo < finals[p][0] <= hi]
    text = ' '.join(f[1] for f in mine).split()
    after = [f[0] for f in mine if f[0] > c['end']]
    lat = (min(after)-c['end'])*1000 if after else None
    if lat is not None:
        latency.append(lat)
    per_clip.append(dict(id=c['id'], errors=ed(c['text'].split(), text), words=len(c['text'].split()),
                         phrases=len(mine), final_latency_ms=None if lat is None else round(lat, 1),
                         hyp=' '.join(text)))
empty = [p for p in order if not finals[p][1].strip()]
stats = dict(re.findall(r'(\w+)=(\d+)', stderr.split('capture_stats', 1)[1].splitlines()[0])) if 'capture_stats' in stderr else {}
board_events = [e for e in board if not e.get('snapshot')]
tcp_live = [e for _, e in tcp if not e.get('snapshot')]
summary = dict(
    clips=len(clips), words=len(ref), final_errors=final_errors, final_wer=round(100*final_errors/len(ref), 2),
    preview_errors=preview_errors, preview_wer=round(100*preview_errors/len(ref), 2),
    phrases=len(order), empty_phrases=len(empty),
    final_latency_ms=pct(latency), final_latency_note='PC speaker start to TCP receipt on this PC, from clip audio end',
    clips_without_final=[c['id'] for c in per_clip if c['final_latency_ms'] is None],
    merges=stderr.count('merge_event'), capture_stats=stats,
    capture_ok=bool(stats) and all(stats.get(k) == '0' for k in
                                   ('sequence_errors', 'ring_overruns', 'consumer_dropped', 'mic_overruns', 'error')),
    board_events=len(board_events), tcp_events=len(tcp_live),
    tcp_complete=tcp_live == board_events[len(board_events)-len(tcp_live):],
    per_clip=per_clip)
json.dump(summary, open(d/'summary.json', 'w'), indent=1)
print(json.dumps({k: v for k, v in summary.items() if k != 'per_clip'}, indent=1))
for c in per_clip:
    if c['errors']:
        print(f"{c['id']}: {c['errors']}/{c['words']}  {c['hyp']}")
