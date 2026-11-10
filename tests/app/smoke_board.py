"""Native board TCP/log/shutdown smoke test. Run under the board mutex."""
import json
import signal
import socket
import subprocess
import time
from pathlib import Path

command=['../build/sw/app/sttcap','--source','cpu-wav','../cpu/clips/1089-134691-0023.wav',
         '--blob','../cpu/quartznet_int8.bin','--prior','../cpu/running_prior.json',
         '--threads','3','--port','8765','--log','tcp-smoke.jsonl']
with open('tcp-smoke.stdout','w') as out:
    process=subprocess.Popen(command,stdout=out)
    try:
        for _ in range(100):
            try:
                connection=socket.create_connection(('127.0.0.1',8765),timeout=1)
                break
            except OSError:
                if process.poll() is not None: raise RuntimeError('app exited')
                time.sleep(.05)
        else:
            raise TimeoutError('caption TCP listener')
        connection.settimeout(5)
        with connection, connection.makefile('r') as stream:
            for _ in range(3):
                event=json.loads(stream.readline())
                assert event['state']=='Speaking'
        process.send_signal(signal.SIGINT)
        assert process.wait(timeout=5)==0
    finally:
        if process.poll() is None:
            process.kill(); process.wait()
events=[json.loads(s) for s in Path('tcp-smoke.jsonl').read_text().splitlines()]
assert len(events)>=3
assert Path('caption.ppm').read_bytes().startswith(b'P6\n640 240\n255\n')
print('PASS TCP JSON events, session log, PPM output and SIGINT shutdown')
