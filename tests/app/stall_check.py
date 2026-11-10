"""Output-stage breakdown from sttcap --stages CSVs (stall_board.sh output).

Usage: python stall_check.py DIR [--threshold-ms 50] [--json OUT]
Per stage, p50/p95/max of the stage duration in ms; 'queue' is inference
completion to output-thread pop; 'path' is pop to the last hot-path stage
(columns up to and including tcp, in CSV order). Columns after tcp are the
writer thread's completions (log, stdout, ppm), reported as '<name>_lag'
relative to tcp. Lists every event whose output path exceeds the threshold.
"""
import argparse
import csv
import json
from pathlib import Path



def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    position = (len(values)-1)*q
    lo = int(position)
    return values[lo] + (values[min(lo+1, len(values)-1)]-values[lo])*(position-lo)


def durations(row, columns):
    """Per-stage ms. Stage times of 0 (state events, PPM not due) are
    skipped; the previous timestamp carries over."""
    columns = columns[2:]
    hot = columns[:columns.index('tcp')+1]
    out, last = {}, None
    for name in columns[len(hot):]:
        t = float(row[name])
        if t > 0 and float(row['tcp']) > 0:
            out[name+'_lag'] = (t-float(row['tcp']))*1000
    for name in hot:
        t = float(row[name])
        if name == 'inference':
            last = t if t > 0 else None
            continue
        if t <= 0:
            continue
        if last is not None:
            out['queue' if name == 'pop' else name] = (t-last)*1000
        last = t
    if 'pop' in row and last is not None:
        out['path'] = (last-float(row['pop']))*1000
    return out


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('directory', type=Path)
    p.add_argument('--threshold-ms', type=float, default=50)
    p.add_argument('--json', type=Path)
    args = p.parse_args()
    samples, slow = {}, []
    files = sorted(args.directory.rglob('*.stages.csv'))
    for path in files:
        reader = csv.DictReader(path.open())
        for row in reader:
            d = durations(row, reader.fieldnames)
            if row['state'] == 'L':
                d.pop('queue', None)  # Listening has no inference time
            for k, v in d.items():
                samples.setdefault(k, []).append(v)
            if d.get('path', 0) > args.threshold_ms:
                slow.append(dict(file=path.name, phrase=int(row['phrase']), state=row['state'],
                                 pop=float(row['pop']), **{k: round(v, 3) for k, v in d.items()}))
    table = {k: dict(n=len(v), p50=percentile(v, .5), p95=percentile(v, .95), max=max(v))
             for k, v in samples.items()}
    result = dict(files=len(files), threshold_ms=args.threshold_ms, stages=table,
                  over_threshold=len(slow), slow=slow)
    for k, v in table.items():
        print(f"{k:8s} n={v['n']:6d} p50={v['p50']:9.3f} p95={v['p95']:9.3f} max={v['max']:9.3f} ms")
    print(f'{len(slow)} events with output path > {args.threshold_ms} ms')
    for s in slow[:40]:
        print(json.dumps(s))
    if args.json:
        args.json.write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__':
    main()
