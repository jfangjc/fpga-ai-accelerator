"""One-process paced replay soak; run under the board mutex.

Usage: python3 soak.py [STTCAP]  (default ../build/sw/app/sttcap). Ten minutes of looped
PCM through paced pl-inject, a loopback TCP client on port 8765, the default
PPM policy and --stages soak.stages.csv; reports RSS, queue depth and the
output-path stage maxima (stall_check.py).
"""
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import wave

from stall_check import durations
import csv

app = sys.argv[1] if len(sys.argv) > 1 else '../build/sw/app/sttcap'
cpu = '/home/ubuntu/stt/cpu'

rate, duration = 16000, 600
files = sorted(Path(f'{cpu}/clips').glob('*.wav'))
with wave.open('soak.wav', 'wb') as out:
    out.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
    count = 0
    while count < rate*duration:
        for path in files:
            with wave.open(str(path), 'rb') as source:
                assert source.getparams()[:3] == (1, 2, rate)
                block = source.readframes(min(source.getnframes(), rate*duration-count))
            out.writeframes(block)
            count += len(block)//2
            if count >= rate*duration:
                break
with open('soak.stdout', 'w', newline='\n') as output:
    proc = subprocess.Popen([app, '--source', 'pl-inject', 'soak.wav',
        '--blob', f'{cpu}/quartznet_int8.bin', '--prior', f'{cpu}/running_prior.json',
        '--threads', '3', '--log', 'soak.jsonl', '--port', '8765',
        '--stages', 'soak.stages.csv'], stdout=output)
    received = []

    def client():
        for _ in range(400):
            try:
                sock = socket.create_connection(('127.0.0.1', 8765))
                break
            except OSError:
                time.sleep(.01)
        else:
            return
        with sock, sock.makefile('r') as stream:
            received.extend(1 for _ in stream)
    tcp = threading.Thread(target=client); tcp.start()
    readings = []
    start = time.monotonic()
    while proc.poll() is None:
        try:
            status = Path(f'/proc/{proc.pid}/status').read_text()
            rss = int(next(s.split()[1] for s in status.splitlines() if s.startswith('VmRSS:')))
            readings.append((time.monotonic()-start, rss))
        except (FileNotFoundError, StopIteration):
            pass
        if len(readings)%30 == 0:
            print('RSS', readings[-1:] or 'process exited', flush=True)
        time.sleep(1)
tcp.join()
events = [json.loads(s) for s in Path('soak.jsonl').read_text().splitlines()]
stages = {}
with open('soak.stages.csv') as f:
    reader = csv.DictReader(f)
    for row in reader:
        d = durations(row, reader.fieldnames)
        if row['state'] == 'L':
            d.pop('queue', None)
        for k, v in d.items():
            stages.setdefault(k, []).append(v)
updates = [e for e in events if not e.get('event_only')]
result = dict(exit=proc.returncode, seconds=time.monotonic()-start,
    rss_kib=readings, max_queue=max(e['queue_depth'] for e in updates),
    lost_frames=max(e['lost_frames'] for e in updates),
    events=len(events), tcp_lines=len(received),
    stage_max_ms={k: max(v) for k, v in stages.items()},
    path_over_50ms=sum(v > 50 for v in stages.get('path', [])),
    queue_over_50ms=sum(v > 50 for v in stages.get('queue', [])),
    update_ms_max=max(e['update_ms'] for e in updates if e.get('latency_valid')))
Path('soak-summary.json').write_text(json.dumps(result, indent=2)+'\n', newline='\n')
print(json.dumps({k:v for k,v in result.items() if k!='rss_kib'}), flush=True)
raise SystemExit(proc.returncode)
