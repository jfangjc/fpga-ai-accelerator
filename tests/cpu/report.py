"""Render final_eval.py JSON to stdout Markdown, without altering result docs.

python3 qnrt/tools/report.py cpu_final.json > cpu_final.md
Percentiles use linear interpolation. Requires only the Python standard library.
"""
import json
import re
import statistics as st
import sys
from peak_log import parse_peak


def pct(xs, p):
    xs = sorted(xs)
    x = (len(xs)-1)*p
    i = int(x)
    return xs[i] + (xs[min(i+1, len(xs)-1)]-xs[i])*(x-i)


def report(data):
    print('CPU measurement status:', 'complete' if data['complete'] else
          'INCOMPLETE (not an accepted final benchmark)')
    print('\nWeights SHA256:', data['weights_sha256'])
    peaks = {nt: parse_peak(data['peak'], 'int8_sched', nt) for nt in [1, 2, 4]}
    print('\nINT8 register-only measured ceiling GMAC/s:', peaks)
    print('\n| Window | Threads | p50 s | p90 s | GMAC/s | % peak | Scaling |')
    print('|---:|---:|---:|---:|---:|---:|---:|')
    base = {}
    for r in data['windows']:
        vals = re.findall(r'run=(\d+) seconds=([\d.]+) macs=(\d+)', r['log'])
        ts = [float(sec) for run, sec, _ in vals if int(run)]
        if len(ts) < 20:
            raise ValueError('fewer than 20 window samples')
        med = st.median(ts)
        rate = int(vals[0][2])/med/1e9
        nt, sec = r['threads'], r['seconds']
        if nt == 1:
            base[sec] = med
        print(f'|{sec}|{nt}|{med:.6f}|{pct(ts,.9):.6f}|{rate:.3f}|'
              f'{100*rate/peaks[nt]:.2f}|{base[sec]/med:.3f}|')
    print('\n| Mode | p50 s | p90 s | Max s | RTF | WER % | RSS MiB |')
    print('|---|---:|---:|---:|---:|---:|---:|')
    splits = []
    for mode in ['utterance', 'running']:
        rows = [r for r in data['clips'] if r['mode'] == mode]
        if len(rows) != 40:
            raise ValueError('expected 40 clips per mode')
        medians = [st.median(t['frontend']+t['network']
                   for t in r['timings']) for r in rows]
        rss = max(t['rss_kib'] for r in rows for t in r['timings'])/1024
        wer = 100*sum(r['errors'] for r in rows)/sum(r['words'] for r in rows)
        rtf = sum(medians)/sum(r['seconds'] for r in rows)
        print(f'|{mode}|{st.median(medians):.6f}|{pct(medians,.9):.6f}|'
              f'{max(medians):.6f}|{rtf:.6f}|{wer:.7f}|{rss:.3f}|')
        for kind in ['frontend', 'network']:
            xs = [st.median(t[kind] for t in r['timings']) for r in rows]
            splits.append(f'{mode} {kind} p50/p90/max seconds: '
                          f'{st.median(xs):.6f}/{pct(xs,.9):.6f}/{max(xs):.6f}')
    print('\n' + '\n\n'.join(splits))
    print('\n| Clip | Mode | p50 s | p90 s | Max s | RTF | Errors/words | Frontend p50 s | Network p50 s | RSS MiB |')
    print('|---|---|---:|---:|---:|---:|---:|---:|---:|---:|')
    for r in data['clips']:
        ts = [t['frontend']+t['network'] for t in r['timings']]
        med = st.median(ts)
        print(f"|{r['id']}|{r['mode']}|{med:.6f}|{pct(ts,.9):.6f}|"
              f"{max(ts):.6f}|{med/r['seconds']:.6f}|{r['errors']}/{r['words']}|"
              f"{st.median(t['frontend'] for t in r['timings']):.6f}|"
              f"{st.median(t['network'] for t in r['timings']):.6f}|"
              f"{max(t['rss_kib'] for t in r['timings'])/1024:.3f}|")
    print('\nTiming excludes model loading, WAV I/O, and CTC rendering. '
          'Clip timing includes frontend and quantization. RSS includes loading. '
          'Network samples exclude the separate profiling pass. '
          'MAC counts include nominal padding. Peak uses matching thread count '
          'for the SMULL/SMLAL2/SADALP register-only instruction mix.')
    if 'phrase' in data:
        phrase = data['phrase']
        samples = [dict((k, float(v)) for k, v in
                   re.findall(r'(\w+)=([\d.]+)', line))
                   for line in phrase['log'].splitlines() if line.startswith('run=')]
        samples = samples[1:]
        if len(samples) < 20:
            raise ValueError('fewer than 20 phrase samples')
        print('\n16 s phrase benchmark:', phrase['path'],
              'duration seconds:', phrase['seconds'])
        print('\n| Component | p50 s | p90 s |')
        print('|---|---:|---:|')
        for kind in ['frontend', 'network', 'total']:
            xs = [s['frontend'] + s['network'] if kind == 'total' else s[kind]
                  for s in samples]
            print(f'|{kind}|{st.median(xs):.6f}|{pct(xs,.9):.6f}|')
        print('\nTranscript:', phrase['hyp'])
        if phrase.get('reference'):
            print('\nReference:', phrase['reference']['text'])
            print(f"\nWER: {phrase['errors']}/{phrase['words']} = "
                  f"{100*phrase['errors']/phrase['words']:.4f}%")
        print('\n16-second phrase:\n```\n'+data['phrase']['log']+'```')
    print('\nCommands:\n```')
    for cmd in data['commands']:
        print(' '.join(cmd))
    print('```\nConditions:\n```\n'+'\n'.join(data['conditions'])+'```')


if __name__ == '__main__':
    report(json.load(open(sys.argv[1])))
