"""Board-side 30 minute, single-process system soak. Caller owns board.lock.

python3 t2_soak.py [seconds=1800]. Evidence stays in the current directory.
The MMIO observer only reads; capture remains the sole register writer.
"""
import csv
import json
import mmap
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading
import time
import wave

BASE = Path('/home/ubuntu/stt/t2')
CPU = Path('/home/ubuntu/stt/cpu')
duration = int(sys.argv[1]) if len(sys.argv) > 1 else 1800
files = sorted((CPU/'clips').glob('*.wav'))
manifest = []
with wave.open('soak.wav', 'wb') as out:
    out.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
    count = 0
    while count < duration*16000:
        for path in files:
            with wave.open(str(path), 'rb') as source:
                assert source.getparams()[:3] == (1, 2, 16000)
                block = source.readframes(min(source.getnframes(), duration*16000-count))
            manifest.append(dict(clip=path.stem, begin_sample=count, samples=len(block)//2))
            count += len(block)//2
            out.writeframes(block)
            if count >= duration*16000:
                break
Path('playlist.json').write_text(json.dumps(manifest, indent=2)+'\n', newline='\n')
uio = next(p.parent.name for p in Path('/sys/class/uio').glob('uio*/name')
           if p.read_text().strip() == 'stt-frontend')
regfile = open('/dev/'+uio, 'rb', buffering=0)
regs = mmap.mmap(regfile.fileno(), 4096, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ)
def reg(offset):
    return struct.unpack_from('<I', regs, offset)[0]

command = [str(BASE/'src/build/sw/app/sttcap'), '--source', 'pl-inject', 'soak.wav',
           '--backend', 'pl', '--program', '/home/ubuntu/stt/i2/program.qnp2',
           '--blob', str(CPU/'quartznet_int8.bin'), '--prior', str(CPU/'running_prior.json'),
           '--threads', '3', '--port', '8765', '--log', 'soak.jsonl',
           '--stages', 'soak.stages.csv', '--ppm', 'caption.ppm']
Path('command.json').write_text(json.dumps(command, indent=2)+'\n', newline='\n')
start = time.monotonic()
stop_power = threading.Event()
def power():
    # Reuse the sensor selection/scaling in tools/power.sh; retain every sample.
    with open('power.jsonl', 'w', buffering=1, newline='\n') as f:
        while not stop_power.is_set():
            before = time.monotonic()
            result = subprocess.run(['sh', str(BASE/'src/tools/power.sh'), '1', '0'],
                                    text=True, capture_output=True, check=True)
            fields = dict(x.split('=') for x in result.stdout.split())
            f.write(json.dumps(dict(seconds=before-start, watts=float(fields['mean_W'])))+'\n')
            stop_power.wait(max(0, .1-(time.monotonic()-before)))

tcp_error = []
def client():
    try:
        for _ in range(1000):
            try:
                sock = socket.create_connection(('127.0.0.1', 8765), timeout=2)
                break
            except OSError:
                time.sleep(.01)
        else:
            raise RuntimeError('TCP did not start')
        sock.settimeout(None)
        with sock, sock.makefile('r') as stream, open('tcp.jsonl', 'w', buffering=1, newline='\n') as f:
            for line in stream:
                f.write(json.dumps(dict(received_monotonic=time.monotonic(), event=json.loads(line)))+'\n')
    except Exception as error:
        tcp_error.append(repr(error))

with open('soak.stdout', 'w', newline='\n') as stdout, open('soak.stderr', 'w', newline='\n') as stderr:
    proc = subprocess.Popen(command, stdout=stdout, stderr=stderr)
    Path('app.pid').write_text(str(proc.pid)+'\n', newline='\n')
    pt = threading.Thread(target=power)
    ct = threading.Thread(target=client)
    pt.start(); ct.start()
    with open('monitor.jsonl', 'w', buffering=1, newline='\n') as f:
        while proc.poll() is None:
            try:
                status = Path(f'/proc/{proc.pid}/status').read_text()
                rss = int(next(s.split()[1] for s in status.splitlines() if s.startswith('VmRSS:')))
                temp_paths = list(Path('/sys/class/thermal').glob('thermal_zone*/temp'))
                temp_paths += list(Path('/sys/class/hwmon').glob('hwmon*/temp*_input'))
                temps = {str(p): int(p.read_text()) for p in temp_paths}
                iio = {str(p): p.read_text().strip() for p in Path('/sys/bus/iio/devices').glob('iio:device*/in_temp*')
                       if p.name.endswith(('_raw', '_offset', '_scale'))}
                f.write(json.dumps(dict(seconds=time.monotonic()-start, rss_kib=rss,
                    control=reg(0), status=reg(4), frames=reg(16), overruns=reg(20), temperatures_mc=temps, iio_temperature=iio))+'\n')
            except (FileNotFoundError, StopIteration):
                pass
            time.sleep(1)
    stop_power.set(); pt.join(); ct.join(timeout=5)
Path('run-result.json').write_text(json.dumps(dict(exit=proc.returncode,
    wall_seconds=time.monotonic()-start, audio_seconds=duration, tcp_errors=tcp_error), indent=2)+'\n', newline='\n')
regs.close(); regfile.close()
raise SystemExit(proc.returncode)
