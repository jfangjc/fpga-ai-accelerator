"""Accepted benchmark collector. Run in ~/stt/cpu on an idle AArch64 board.

python3 qnrt/tools/final_eval.py [--runs 20] [--phrase path/to/16s.wav]
All results are saved incrementally in cpu_final.json; failures leave complete=false.
The optional phrase must be real 16-second audio; no synthetic phrase is substituted.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile
import time
import wave


def run(args):
    p = subprocess.run(args, capture_output=True, text=True, check=True)
    return p.stdout, p.stderr


def conditions():
    return run(['sh', '-c', '''date -u; uname -a; uptime
pgrep -a -f 'fe|soak|loopback' || true
top -bn1 | head -15
for pattern in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq /sys/class/thermal/thermal_zone*/temp; do
  if test -r "$pattern"; then echo "$pattern"; cat "$pattern"; else echo "unavailable: $pattern"; fi
done'''])[0]


def idle():
    # Check current load, not lifetime ps %CPU. Refuse shared integration jobs
    # even when they use little CPU. Never stop them or alter PL state.
    ps = run(['ps', '-eo', 'pid=,comm=,args='])[0]
    for line in ps.splitlines():
        fields = line.split(None, 2)
        if len(fields) < 3 or int(fields[0]) == os.getpid():
            continue
        comm, args = fields[1:]
        if (re.search(r'soak|loopback|paced.replay', args, re.I)
                or comm.startswith('qnrt_') or comm == 'peak'):
            raise RuntimeError('competing workload: ' + line)
    def ticks():
        return [list(map(int, x.split()[1:9])) for x in
                Path('/proc/stat').read_text().splitlines()
                if re.match(r'cpu\d+ ', x)]
    before = ticks()
    time.sleep(2)
    after = ticks()
    for core, (a, b) in enumerate(zip(before, after)):
        d = [y-x for x, y in zip(a, b)]
        busy = 1 - (d[3]+d[4])/max(1, sum(d))
        if busy > .10:
            raise RuntimeError(f'core {core} busy ({busy:.0%}); defer benchmark')


def errors(ref, hyp):
    row = list(range(len(hyp)+1))
    for i, a in enumerate(ref, 1):
        nxt = [i]
        for j, b in enumerate(hyp, 1):
            nxt.append(min(nxt[-1]+1, row[j]+1, row[j-1]+(a != b)))
        row = nxt
    return row[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', type=int, default=20)
    ap.add_argument('--phrase', type=Path)
    ap.add_argument('--phrase-reference', type=Path)
    opts = ap.parse_args()
    if opts.runs < 20 or platform.machine() != 'aarch64':
        ap.error('accepted measurements require aarch64 and at least 20 runs')
    rows = json.loads(Path('clips/clips.json').read_text())
    if len(rows) != 40:
        raise RuntimeError('expected exactly 40 clips')
    data = dict(complete=False, windows=[], clips=[], checks={}, conditions=[],
                commands=[], weights_sha256=hashlib.sha256(
                    Path('quartznet_int8.bin').read_bytes()).hexdigest())
    def save():
        Path('cpu_final.json').write_text(json.dumps(data, indent=2))
    def measured(cmd):
        for attempt in range(10):
            try:
                idle()
                data['conditions'].append(conditions())
                data['commands'].append(cmd)
                save()
                out = run(cmd)
                idle()
                data['conditions'].append(conditions())
                return out
            except RuntimeError as exc:
                # Reject the entire sample batch; never retain busy results.
                data.setdefault('rejected', []).append(dict(command=cmd, reason=str(exc)))
                save()
                print('Deferred/rejected:', exc, flush=True)
                time.sleep(2)
        raise RuntimeError('board did not remain idle after ten attempts')
    save()
    for nt in [1, 2, 4]:
        out, err = measured(['./build/sw/qnrt/qnrt_test', 'quartznet_int8.bin',
                             'vectors.tsv', str(nt)])
        if 'PASS 558 layer vectors' not in out:
            raise RuntimeError('missing layer validation')
        data['checks'][str(nt)] = out + err
        save()
    for name in ['malformed.py', 'arithmetic.py', 'verify_full.py']:
        out, err = measured(['python3', 'qnrt/tools/' + name])
        data['checks'][name] = out + err
        save()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # Extract features once; all window runs use the exact same bytes.
        source = rows[0]['id']
        measured(['./build/sw/qnrt/qnrt_transcribe', 'quartznet_int8.bin',
                  f'clips/{source}.wav', '--features', str(tmp/'source.f32')])
        quant = (tmp/'source.f32.int8').read_bytes()
        stride = len(quant)//64
        for seconds in [2, 3, 4]:
            valid = seconds*100  # exact existing 3s baseline: 300 valid
            frames = (valid+15)//16*16
            inp = tmp/'window.i8'
            inp.write_bytes(b''.join(quant[c*stride:c*stride+valid] +
                                     bytes(frames-valid) for c in range(64)))
            for nt in [1, 2, 4]:
                out, err = measured(['./build/sw/qnrt/qnrt_bench', 'quartznet_int8.bin',
                                    str(inp), str(frames), str(valid), str(nt),
                                    str(opts.runs)])
                data['windows'].append(dict(seconds=seconds, frames=frames,
                    valid=valid, threads=nt, log=out+err,
                    input_sha256=hashlib.sha256(inp.read_bytes()).hexdigest()))
                save()
    out, err = measured(['./build/sw/qnrt/peak'])
    data['peak'] = out + err
    perf = subprocess.run(['sh', '-c',
        'perf stat -e cycles,instructions,cache-misses true'],
        text=True, capture_output=True)
    data['perf_availability'] = perf.stdout + perf.stderr
    for mode in ['utterance', 'running']:
        for row in rows:
            cmd = ['./build/sw/qnrt/qnrt_transcribe', 'quartznet_int8.bin',
                   'clips/'+row['id']+'.wav', '--mode', mode, '--threads', '4',
                   '--prior', 'running_prior.json', '--bench']
            out, err = measured(cmd)
            timings = [dict((k, float(v)) for k, v in
                       re.findall(r'(\w+)=([\d.]+)', line))
                       for line in err.splitlines() if line.startswith('run=')]
            if len(timings) != 6:
                raise RuntimeError('expected warmup plus five clip samples')
            data['clips'].append(dict(id=row['id'], mode=mode, hyp=out.strip(),
                errors=errors(row['text'].split(), out.split()),
                words=len(row['text'].split()), seconds=row['seconds'],
                timings=timings[1:]))
            save()
    if opts.phrase:
        with wave.open(str(opts.phrase)) as w:
            duration = w.getnframes() / w.getframerate()
            if w.getframerate() != 16000 or not 15.5 <= duration <= 16.5:
                raise RuntimeError('phrase must contain approximately 16s at 16kHz')
        out, err = measured(['./build/sw/qnrt/qnrt_transcribe', 'quartznet_int8.bin',
                            str(opts.phrase), '--runs', str(opts.runs)])
        reference = json.loads(opts.phrase_reference.read_text()) if opts.phrase_reference else None
        data['phrase'] = dict(path=str(opts.phrase), seconds=duration,
                             hyp=out.strip(), log=err, reference=reference)
        if reference:
            data['phrase']['errors'] = errors(reference['text'].split(), out.split())
            data['phrase']['words'] = len(reference['text'].split())
    data['complete'] = bool(opts.phrase)
    save()
    print('Saved cpu_final.json; complete=', data['complete'], flush=True)


if __name__ == '__main__':
    # Shared with front-end replay/soak workers. Hold across the entire run,
    # including condition snapshots and every timing subprocess.
    with (Path.home() / 'stt/board.lock').open('r') as board_lock:
        print('Waiting for ~/stt/board.lock', flush=True)
        fcntl.flock(board_lock, fcntl.LOCK_EX)
        main()
