"""Fit hardware FRAMES / CLOCK_MONOTONIC pairs, independent of caption age.

Use either monitor.jsonl (one-second, quantised counters), or app stderr
capture_clock edge brackets. Exclude startup/drain and report regression
residuals and five-minute rates rather than trusting clock-tree metadata.
"""
import argparse
import json
from pathlib import Path
import re
import statistics


def fit(rows):
    x0, y0 = rows[0]
    xs = [x-x0 for x, y in rows]
    ys = [y-y0 for x, y in rows]
    xm, ym = statistics.mean(xs), statistics.mean(ys)
    slope = sum((x-xm)*(y-ym) for x, y in zip(xs, ys))/sum((x-xm)**2 for x in xs)
    residual = [(y-ym-slope*(x-xm))/slope*1000 for x, y in zip(xs, ys)]
    slope_se = (sum((r*slope/1000)**2 for r in residual)/(len(rows)-2)/
                sum((x-xm)**2 for x in xs))**.5
    return dict(pairs=len(rows), seconds=xs[-1], sample_rate=slope,
                ppm=(slope/16000-1)*1e6, ppm_standard_error=slope_se/16000*1e6,
                residual_ms_min=min(residual),
                residual_ms_max=max(residual), residual_ms_rms=statistics.mean(r*r for r in residual)**.5)


def analyze(path):
    if path.suffix == '.jsonl':
        rows = [json.loads(s) for s in path.read_text().splitlines()]
        end = rows[-1]['seconds']
        pairs = [(r['seconds'], r['frames']*160) for r in rows
                 if 5 <= r['seconds'] <= end-5 and r['frames'] > 0]
    else:
        pattern = r'capture_clock mono=([\d.]+) frames=(\d+)'
        pairs = [(float(x), int(y)*160) for x, y in re.findall(pattern, path.read_text())]
    result = fit(pairs)
    result['buckets'] = []
    start = pairs[0][0]
    for bucket in range(int((pairs[-1][0]-start)//300)+1):
        part = [p for p in pairs if bucket*300 <= p[0]-start < (bucket+1)*300]
        if len(part) > 2:
            result['buckets'].append(dict(minutes=f'{bucket*5}-{(bucket+1)*5}', **fit(part)))
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('input', type=Path)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    result = analyze(a.input)
    text = json.dumps(result, indent=2)+'\n'
    if a.output:
        a.output.write_text(text)
    print(text)
