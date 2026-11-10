"""Demo replay/evidence helper; demo_board.sh owns the board lock."""
import json
import mmap
import hashlib
from pathlib import Path
import struct
import subprocess
import sys
import shutil
import time
import wave

backend, tag, *clips = sys.argv[1:]
base = Path('/home/ubuntu/stt/t2')
out = base/'demo'/tag
out.mkdir(parents=True, exist_ok=False)
evidence = out/'evidence'
evidence.mkdir()
cpu = Path('/home/ubuntu/stt/cpu')
with wave.open(str(out/'demo.wav'), 'wb') as target:
    target.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
    for clip in clips:
        with wave.open(str(cpu/'clips'/f'{clip}.wav'), 'rb') as source:
            assert source.getparams()[:3] == (1, 2, 16000)
            target.writeframes(source.readframes(source.getnframes()))
        target.writeframes(bytes(25600))
app = base/'src/build/sw/app'/('sttcap' if backend == 'pl' else 'sttcap-cpu')
cmd = [str(app), '--source', 'pl-inject' if backend == 'pl' else 'cpu-wav', str(out/'demo.wav'),
       '--backend', backend, '--blob', str(cpu/'quartznet_int8.bin'),
       '--prior', str(cpu/'running_prior.json'), '--threads', '3', '--port', '8765',
       '--log', 'session.jsonl', '--stages', 'stages.csv', '--ppm', 'caption.ppm']
if backend == 'pl':
    cmd += ['--program', '/home/ubuntu/stt/i2/program.qnp2']
(out/'command.json').write_text(json.dumps(cmd, indent=2)+'\n', newline='\n')
(out/'assets.json').write_text(json.dumps({str(p): hashlib.sha256(p.read_bytes()).hexdigest()
    for p in (app, cpu/'quartznet_int8.bin', cpu/'running_prior.json')}, indent=2)+'\n', newline='\n')
regfile = regs = None
if backend == 'pl':
    uio = next(p.parent.name for p in Path('/sys/class/uio').glob('uio*/name')
               if p.read_text().strip() == 'stt-frontend')
    regfile = open('/dev/'+uio, 'rb', buffering=0)
    regs = mmap.mmap(regfile.fileno(), 4096, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ)
with open(out/'stdout.txt', 'w', newline='\n') as stdout, open(out/'stderr.txt', 'w', newline='\n') as stderr:
    proc = subprocess.Popen(cmd, cwd=out, stdout=stdout, stderr=stderr)
    started = time.monotonic()
    previous = None
    with open(out/'ui.jsonl', 'w', buffering=1, newline='\n') as ui:
        while proc.poll() is None:
            if regs:
                control, status = struct.unpack_from('<II', regs)
                state = (bool(control & 64), bool(status & 8))
                if state != previous:
                    ui.write(json.dumps(dict(monotonic=time.monotonic(), control=control,
                        status=status, led=state[0], sticky_button=state[1]))+'\n')
                    previous = state
            if time.monotonic()-started > 180:
                # Only the child launched by this helper is stopped.
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait()
                raise TimeoutError('demo exceeded 180 seconds')
            time.sleep(.01)
if regs:
    regs.close(); regfile.close()
fb = Path('/dev/fb0')
if fb.exists():
    info = Path('/sys/class/graphics/fb0')
    (out/'snapshot.fbinfo').write_text(''.join(name+'='+ (info/name).read_text()
        for name in ('virtual_size', 'bits_per_pixel', 'stride', 'pan')), newline='\n')
(out/'result.json').write_text(json.dumps(dict(exit=proc.returncode, backend=backend, clips=clips,
    wall_seconds=time.monotonic()-started, source='pl-inject' if backend=='pl' else 'cpu-wav',
    lock='board.lock held by parent demo_board.sh', stop='natural replay EOF and thread joins'))+'\n', newline='\n')
# Keep replay audio and duplicate stdout on the board; copy only reviewable evidence.
for name in ('command.json', 'assets.json', 'session.jsonl', 'stages.csv', 'caption.ppm',
             'stderr.txt', 'ui.jsonl', 'snapshot.fbinfo', 'result.json'):
    shutil.copyfile(out/name, evidence/name)
raise SystemExit(proc.returncode)
