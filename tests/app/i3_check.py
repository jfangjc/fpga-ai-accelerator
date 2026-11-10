"""I3 check: PL-backend captions against the CPU backend and the Python INT8 golden.

Usage (host, repository .venv):
  i3_check.py RUN_DIR [--clips ID...] [--json OUT]
RUN_DIR holds, per clip, <clip>.pl.jsonl and <clip>.cpu.jsonl (sttcap --log)
and <clip>.pl.dump/ (sttcap --dump: final-N.i8, final-N.logits, finals.tsv).

Per clip this checks:
  1. network: every PL final's padded logits (all allocated frames) equal
     golden_int8.network on the exact INT8 features the app submitted;
  2. PL finals equal CPU-backend finals (phrase by phrase, same segmentation);
  3. Python pipeline: stream_sim.phrases on the replayed clip (+0.8 s tail)
     gives the same phrase ends and valid lengths, and the Python INT8 final
     (frontend.features -> quantize -> golden_int8.network -> greedy CTC)
     equals the PL final text.
Exit status is nonzero if any check fails.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'model'))
import pickle  # noqa: E402
import stream_sim as sim  # noqa: E402
from frontend import features  # noqa: E402
from golden_int8 import network  # noqa: E402


def finals(path):
    events = [json.loads(line) for line in path.read_text().splitlines()]
    return {e['phrase']: e for e in events
            if e['state'] == 'Committed' and not e.get('event_only')}, events


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('run', type=Path)
    p.add_argument('--clips', nargs='*')
    p.add_argument('--json', type=Path)
    args = p.parse_args()
    sim.enable_exact_blas()
    with (ROOT/'data/int8.pkl').open('rb') as f:
        q = pickle.load(f)
    layers = q['layers']
    last = layers[-1]['name']
    clips = args.clips or sorted(x.name[:-9] for x in args.run.glob('*.pl.jsonl'))
    report, ok = {}, True
    for clip in clips:
        pl, pl_events = finals(args.run/f'{clip}.pl.jsonl')
        cpu, _ = finals(args.run/f'{clip}.cpu.jsonl')
        r = dict(phrases=len(pl), network_exact=0, logit_bytes=0, engine={},
                 pl_equals_cpu=0, python_equals_pl=0, segmentation_equal=True,
                 previews=sum(e['state'] == 'Speaking' and not e.get('event_only')
                              for e in pl_events),
                 merged=max((e.get('merged', 0) for e in pl_events), default=0))
        dump = args.run/f'{clip}.pl.dump'
        for line in (dump/'finals.tsv').read_text().splitlines():
            phrase, t, n, stride, valid, engine = line.split('\t')
            phrase, t, n, stride, valid = map(int, (phrase, t, n, stride, valid))
            x = np.fromfile(dump/f'final-{phrase}.i8', np.int8).reshape(64, t)
            got = (dump/f'final-{phrase}.logits').read_bytes()
            tensors, lengths = network(x, layers, capture=True, length=n)
            exact = (got == tensors[last].tobytes() and valid == lengths[last]
                     and stride == tensors[last].shape[1])
            r['network_exact'] += exact
            r['logit_bytes'] += len(got)
            r['engine'][engine] = r['engine'].get(engine, 0)+1
            ok &= exact
            # CPU backend dump of the same phrase: identical input bytes and
            # identical padded logits (qnrt vs PL engine).
            cpu_dump = args.run/f'{clip}.cpu.dump'
            if (cpu_dump/f'final-{phrase}.i8').exists():
                same_in = (cpu_dump/f'final-{phrase}.i8').read_bytes() == x.tobytes()
                same_out = (cpu_dump/f'final-{phrase}.logits').read_bytes() == got
                r['cpu_input_identical'] = r.get('cpu_input_identical', 0)+same_in
                r['cpu_logits_identical'] = r.get('cpu_logits_identical', 0)+same_out
                ok &= same_in and same_out
        # Python pipeline on the same replayed audio.
        path = next((ROOT/'data/LibriSpeech').rglob(clip+'.flac'))
        _, x = sim.replay_audio(dict(path=path))
        # sttcap numbers every phrase but skips finals of <= FUTURE_SAMPLES.
        python = [(k, begin, finish) for k, (begin, _, finish) in enumerate(sim.phrases(x))
                  if finish-begin > sim.policy.FUTURE_SAMPLES]
        r['python_phrases'] = len(python)
        texts = []
        r['feature_bytes_differing'] = 0
        for k, begin, finish in python:
            audio = x[begin:finish]
            n = len(audio)//sim.BLOCK+1
            qx = sim.quantize(features(audio)[:, :n], q)
            # Python vs C (sttcap) front end: INT8 input bytes that differ.
            dumped = dump/f'final-{k}.i8'
            if dumped.exists():
                app = np.fromfile(dumped, np.int8).reshape(64, -1)
                if app.shape == qx.shape:
                    r['feature_bytes_differing'] += int((app != qx).sum())
            ids = network(qx, layers, length=n).argmax(0)
            text = sim.ctc_text(ids)
            texts.append(text)
            e = pl.get(k)
            if e is None or round(e['capture']*16000) != finish or e['feature_count'] != n:
                r['segmentation_equal'] = False
            r['python_equals_pl'] += e is not None and e['final'] == text
        for k, e in pl.items():
            r['pl_equals_cpu'] += k in cpu and cpu[k]['final'] == e['final']
        # TCP stream (client connected on loopback during the PL run).
        tcp_path = args.run/f'{clip}.pl.tcp.jsonl'
        if tcp_path.exists():
            tcp, tcp_events = finals(tcp_path)
            r['tcp_events'] = len(tcp_events)
            r['tcp_finals_equal'] = sum(k in tcp and tcp[k]['final'] == e['final']
                                        for k, e in pl.items())
            ok &= r['tcp_finals_equal'] == len(pl)
        speaking = [e for e in pl_events if e['state'] == 'Speaking' and not e.get('event_only')]
        r['preview_compute_ms'] = [e['compute_ms'] for e in speaking]
        r['preview_update_ms'] = [e['update_ms'] for e in speaking if e.get('latency_valid')]
        r['commands'] = [pl[k].get('commands') for k in sorted(pl)]
        r['pl_final'] = ' '.join(pl[k]['final'] for k in sorted(pl))
        r['cpu_final'] = ' '.join(cpu[k]['final'] for k in sorted(cpu))
        r['python_final'] = ' '.join(texts)
        r['final_compute_ms'] = [pl[k]['compute_ms'] for k in sorted(pl)]
        r['pass'] = (r['network_exact'] == r['phrases'] and r['segmentation_equal']
                     and len(cpu) == len(pl) == len(python)
                     and r['pl_equals_cpu'] == r['phrases'] == r['python_equals_pl'])
        ok &= r['pass']
        report[clip] = r
        print(clip, 'PASS' if r['pass'] else 'FAIL',
              f"phrases={r['phrases']} network_exact={r['network_exact']} "
              f"logit_bytes={r['logit_bytes']} engine={r['engine']} "
              f"pl==cpu={r['pl_equals_cpu']} pl==python={r['python_equals_pl']} "
              f"segmentation={r['segmentation_equal']} "
              f"python_feature_bytes_differing={r['feature_bytes_differing']}", flush=True)
        print('  ', r['pl_final'], flush=True)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2)+'\n')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
