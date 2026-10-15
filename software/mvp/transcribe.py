"""Transcribe one bounded WAV with the physical KV260 accelerator."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))


def validate_samples(count):
    if not 320 <= count <= 40959:
        raise ValueError('Expected 320 to 40959 samples at 16 kHz. Split longer recordings explicitly.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audio', type=Path)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--runs',type=int,default=1,help='Warm calls after the separately reported cold call')
    parser.add_argument('--verify-cpu',action='store_true',help='Compare every warm logit with the INT8 CPU graph')
    args = parser.parse_args()
    if args.runs < 1 or args.out and args.out.exists():
        parser.error('Use a positive run count and a new output path')
    from integrity import verify_assets
    verify_assets(ROOT)
    from frontend import read_audio
    audio = read_audio(args.audio)
    validate_samples(len(audio))
    from board import check_active_branch, configure_irqs
    check_active_branch(ROOT)
    import numpy as np
    from runtime import graphs, session, AudioTimer
    policies = json.loads((ROOT/'config/policies.json').read_text())
    context = min((.5,1,2.5), key=lambda n:abs(n-len(audio)/16000))
    policy = next(row['policy'] for row in policies['selected'] if row['context_seconds']==context)
    affinity = os.sched_getaffinity(0)
    try:
        start = time.perf_counter()
        cpu_graph, fpga_graph = graphs()
        os.sched_setaffinity(0,{3})
        configure_irqs(policy['irq_core'])
        physical = session(fpga_graph,policy,True)
        cpu = session(cpu_graph,policies['cpu_policies'][format(context,'g')]) if args.verify_cpu else None
        timer = AudioTimer()
        initialization = time.perf_counter()-start
        cold, _ = timer.call(physical,audio,cold=True)
        rows = []
        for _ in range(args.runs):
            row, actual = timer.call(physical,audio)
            if cpu is not None:
                expected_row, expected = timer.call(cpu,audio,False)
                np.testing.assert_array_equal(actual,expected)
                if row['length'] != expected_row['length'] or row['text'] != expected_row['text']:
                    raise RuntimeError('CPU text or length mismatch')
            rows.append(row)
        result = {'hardware_execution':True,'policy_context_seconds':context,
                  'audio_seconds':len(audio)/16000,'initialization_seconds':initialization,
                  'cold':cold,'warm_runs':rows,'warm_p50_seconds':float(np.median([r['seconds'] for r in rows])),
                  'all_warm_logits_bit_exact':True if args.verify_cpu else None,
                  'capture_qualified':False,'representative_wer_qualified':False}
        if args.out:
            args.out.parent.mkdir(parents=True,exist_ok=True)
            with args.out.open('x',encoding='utf-8') as stream:
                stream.write(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2))
    finally:
        os.sched_setaffinity(0,affinity)


if __name__ == '__main__':
    main()
