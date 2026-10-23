"""Compare committed and provisional previews using the same paced replay.

Run under run_moonshine_board.py for physical hybrid measurements. Outputs are
new files; this script does not change the board's hardware configuration.
"""
import argparse
import json
import math
from pathlib import Path
import numpy as np
import subprocess
import sys

from report_moonshine_latency import report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--wav', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--ort-decoder', type=Path, required=True)
    p.add_argument('--ort-encoder', type=Path, required=True)
    p.add_argument('--backend', choices=('fpga', 'cpu-int8'), default='fpga')
    p.add_argument('--library', default='build/native/libqnc_moonshine.so')
    p.add_argument('--native-projections', action='store_true')
    p.add_argument('--threads', type=int, default=2)
    p.add_argument('--decoder-step-threads', type=int)
    p.add_argument('--decoder-generic-step', action='store_true')
    p.add_argument('--native-threads', type=int, default=4)
    p.add_argument('--preview-seconds', type=float, default=1, help='Provisional preview context; committed baseline uses two seconds')
    p.add_argument('--preview-overlap-seconds', type=float, default=.5)
    p.add_argument('--chunk-ms', type=int, default=250)
    p.add_argument('--highpass-hz', type=float, default=30)
    p.add_argument('--provisional-only', action='store_true')
    p.add_argument('--target-p95-ms', type=float, default=500,
                   help='Paced oldest-update age target; use 300 for the experimental fast profile')
    a = p.parse_args()
    if not math.isfinite(a.target_p95_ms) or a.target_p95_ms <= 0:
        p.error('--target-p95-ms must be finite and positive')
    if a.native_projections and a.backend != 'fpga':
        p.error('--native-projections requires --backend fpga')
    a.out.mkdir(parents=True, exist_ok=False)
    results = {}
    modes = [('provisional', '--provisional-preview')]
    if not a.provisional_only:
        modes.insert(0, ('committed', '--accuracy'))
    for name, flag in modes:
        events = a.out/(name+'.jsonl')
        command = [sys.executable, '-m', 'moonshine.live', '--size', 'tiny', '--backend', a.backend,
            flag, '--wav', str(a.wav), '--ort-decoder', str(a.ort_decoder),
            '--ort-encoder', str(a.ort_encoder), '--native-threads', str(a.native_threads),
            '--preview-seconds', str(a.preview_seconds if name == 'provisional' else 2),
            '--preview-overlap-seconds', str(a.preview_overlap_seconds if name == 'provisional' else 1), '--out', str(events)]
        command += ['--highpass-hz', str(a.highpass_hz)]
        command += ['--library', a.library, '--threads', str(a.threads)]
        if a.native_projections:
            command += ['--native-projections']
        if a.decoder_step_threads is not None:
            command += ['--decoder-step-threads', str(a.decoder_step_threads)]
        if a.decoder_generic_step:
            command += ['--decoder-generic-step']
        if name == 'provisional':
            command += ['--chunk-ms', str(a.chunk_ms)]
        with (a.out/(name+'.log')).open('w') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
        rows = [json.loads(line) for line in events.read_text().splitlines()]
        result = report(rows)
        partials = [r for r in rows if r['type'] == 'partial']
        result['partial_mean_processing_ms'] = sum(r['processing_seconds'] for r in partials)*1000/max(1, len(partials))
        compute = [r['processing_seconds']*1000 for r in partials]
        result['partial_p95_processing_ms'] = float(np.percentile(compute, 95)) if compute else None
        result['partial_over_interval_count'] = sum(ms > (a.chunk_ms if name == 'provisional' else 400) for ms in compute)
        result['partial_nonempty_count'] = sum(bool(r['text'].strip()) for r in partials)
        result['partial_token_limit_count'] = sum(bool(r.get('token_limit_reached')) for r in partials)
        result['final_texts'] = [r['text'] for r in rows if r['type'] == 'final']
        result['target_p95_update_age_ms'] = a.target_p95_ms
        age = result['partial_oldest_update_age_ms']
        result['target_passed'] = bool(result['clean_completion'] and result['live_timing_eligible']
            and age and age['p95'] < a.target_p95_ms)
        (a.out/(name+'-report.json')).write_text(json.dumps(result, indent=2)+'\n')
        results[name] = result
        print(json.dumps({'mode': name, **result}), flush=True)
    (a.out/'comparison.json').write_text(json.dumps(results, indent=2)+'\n')


if __name__ == '__main__':
    main()
