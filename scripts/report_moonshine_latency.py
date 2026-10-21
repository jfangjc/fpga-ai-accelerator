"""Summarize live JSONL timing; no word-aligned latency is inferred."""
import argparse
import json
from pathlib import Path

import numpy as np


def report(rows):
    ready = next((r for r in rows if r['type'] == 'ready'), None)
    summaries = [r for r in rows if r['type'] == 'summary']
    summary = summaries[-1] if summaries else None
    partials = [r for r in rows if r['type'] == 'partial']
    finals = [r for r in rows if r['type'] == 'final']
    def distribution(key, selected=None):
        values = [r[key] for r in (partials if selected is None else selected) if key in r]
        return ({'count': len(values), 'p50': float(np.percentile(values, 50)),
                 'p95': float(np.percentile(values, 95)), 'max': max(values)} if values else None)
    # A quick decoder can still leave long gaps between caption updates.
    gaps = [(b['emitted_monotonic_ns']-a['emitted_monotonic_ns'])/1e6
            for a, b in zip(partials, partials[1:])
            if a['utterance_id'] == b['utterance_id']
            and 'emitted_monotonic_ns' in a and 'emitted_monotonic_ns' in b]
    return {'ready': ready, 'summary': summary, 'partial_count': len(partials),
            'final_count': len(finals),
            'partial_source_end_lag_ms': distribution('lag_ms'),
            'partial_oldest_update_age_ms': distribution('oldest_update_age_ms'),
            'final_source_end_lag_ms': distribution('lag_ms', finals),
            'refined_finals': sum(bool(r.get('refined')) for r in finals),
            'max_within_utterance_partial_gap_ms': max(gaps) if gaps else None,
            'clean_completion': bool(summary and summary.get('exit_code') == 0
                and not summary.get('incomplete') and not summary.get('pending_refinements')
                and not any(r['type'] == 'error' for r in rows)),
            'live_timing_eligible': bool(ready and ready.get('paced')
                and partials and all('source_end_ns' in r for r in partials)),
            'timing_scope': 'Paced acquisition to result; oldest update age adds update audio duration '
                            'and encoder lookahead. Neither metric measures aligned word latency.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('events', type=Path)
    p.add_argument('--out', type=Path)
    a = p.parse_args()
    result = report([json.loads(line) for line in a.events.read_text().splitlines() if line.strip()])
    text = json.dumps(result, indent=2)+'\n'
    if a.out:
        with a.out.open('x', encoding='utf-8') as f:
            f.write(text)
    print(text, end='')


if __name__ == '__main__':
    main()
