"""Run the physical ASR command and verify bulk rollback on exit."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import uuid
from board import BRANCH, driver, irqs, preflight, state
from setup_board import activate_bulk
from integrity import ROOT


def restore_irqs(before):
    old=sorted(before,key=int)
    current=sorted(irqs(),key=int)
    if len(old)!=len(current):
        raise RuntimeError('DMA IRQ count changed')
    for source,target in zip(old,current):
        (Path('/proc/irq')/target/'smp_affinity_list').write_text(before[source]['requested'])
    if [irqs()[n]['requested'] for n in current]!=[before[n]['requested'] for n in old]:
        raise RuntimeError('Failed to restore DMA IRQ masks')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('arguments',nargs=argparse.REMAINDER,help='WAV path and transcribe.py options')
    args=parser.parse_args()
    arguments=args.arguments[1:] if args.arguments[:1]==['--'] else args.arguments
    if not arguments or os.geteuid()!=0:
        parser.error('Use sudo .venv/bin/python scripts/run_board.py -- clip.wav [options]')
    preflight(ROOT)
    before=state(ROOT)
    output=ROOT/'reports/local'
    output.mkdir(parents=True,exist_ok=True)
    label=uuid.uuid4().hex
    (output/f'before-{label}.json').write_text(json.dumps(before,indent=2)+'\n')
    def terminate(signum,frame):
        raise SystemExit(128+signum)
    signal.signal(signal.SIGTERM,terminate)
    try:
        subprocess.run(['rmmod','qn_tile'],check=True)
        subprocess.run(['xmutil','unloadapp'],check=True)
        subprocess.run(['xmutil','loadapp',BRANCH],check=True)
        (Path('/sys/bus/platform/devices')/(BRANCH+'-clock')/'set_rate').write_text('166666666')
        subprocess.run(['insmod',str(driver(ROOT,True))],check=True)
        state(ROOT,True)
        subprocess.run([sys.executable,str(ROOT/'software/mvp/transcribe.py'),*arguments],check=True)
    finally:
        # Failure here propagates even if inference succeeded.
        activate_bulk()
        restore_irqs(before['irq'])
        after=state(ROOT)
        if after['driver_sha256']!=before['driver_sha256']:
            raise RuntimeError('Restored driver changed')
        (output/f'restored-{label}.json').write_text(json.dumps(after,indent=2)+'\n')
        print('PASS: bulk firmware, driver, clock and IRQ masks restored')


if __name__=='__main__':
    main()
