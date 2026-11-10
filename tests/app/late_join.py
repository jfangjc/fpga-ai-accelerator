"""TCP late-join check on the board; run under the mutex from ~/stt/out/src/sw/app.

Client A connects before the first event; client B connects 5 s into a paced
pl-inject run. Checks: A receives the initial Listening event; B's first line
arrives promptly (< 50 ms after connect, without waiting for a caption
event), is marked snapshot and equals A's latest line before the join; B
then receives exactly A's subsequent lines. Usage: python3 late_join.py STTCAP CLIP
"""
import json
import socket
import subprocess
import sys
import threading
import time

app, clip = sys.argv[1], sys.argv[2]
cpu = '/home/ubuntu/stt/cpu'
command = [app, '--source', 'pl-inject', f'{cpu}/clips/{clip}.wav', '--blob', f'{cpu}/quartznet_int8.bin',
           '--prior', f'{cpu}/running_prior.json', '--threads', '3', '--port', '8765',
           '--log', 'late-join.jsonl', '--no-ppm']


def connect():
    for _ in range(400):
        try:
            return socket.create_connection(('127.0.0.1', 8765))
        except OSError:
            time.sleep(.005)
    raise TimeoutError('listener')


def reader(sock, lines):
    with sock, sock.makefile('r') as stream:
        for line in stream:
            lines.append((time.monotonic(), line))


process = subprocess.Popen(command, stdout=subprocess.DEVNULL)
a_lines, b_lines = [], []
a = connect()
ta = threading.Thread(target=reader, args=(a, a_lines)); ta.start()
time.sleep(5)
b_connect = time.monotonic()
b = connect()
tb = threading.Thread(target=reader, args=(b, b_lines)); tb.start()
time.sleep(.2)
assert process.wait(timeout=120) == 0
ta.join(); tb.join()
first_time, first = b_lines[0]
snap = json.loads(first)
a_events = [json.loads(s) for _, s in a_lines]
latest = [s for t, s in a_lines if t < b_connect]
result = dict(clip=clip, a_events=len(a_lines), b_events=len(b_lines),
              a_first_state=a_events[0]['state'],
              b_first_snapshot=bool(snap.get('snapshot')),
              b_first_latency_ms=round((first_time-b_connect)*1000, 3),
              snapshot_caption=snap.get('caption'),
              snapshot_equals_latest=json.dumps({k: v for k, v in snap.items() if k != 'snapshot'}, sort_keys=True)
              == json.dumps(json.loads(latest[-1]), sort_keys=True) if latest else None,
              b_tail_equals_a=[s for _, s in b_lines[1:]] == [s for t, s in a_lines if t >= b_connect][-(len(b_lines)-1):]
              if len(b_lines) > 1 else None)
result['pass'] = (result['a_first_state'] == 'Listening' and result['b_first_snapshot']
                  and result['b_first_latency_ms'] < 50 and result['snapshot_equals_latest']
                  and result['b_tail_equals_a'])
print(json.dumps(result))
raise SystemExit(0 if result['pass'] else 1)
