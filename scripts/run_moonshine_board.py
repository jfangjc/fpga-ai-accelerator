"""Load qualified FPGA hardware for one session; restore the idle starter kit."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys

import hashlib

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def execute(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT)


def active():
    apps = [line.split()[-1] for line in execute('xmutil', 'listapps').splitlines()
            if re.search(r'\d+->\d+', line)]
    if len(apps) > 1:
        raise RuntimeError('Ambiguous active FPGA application')
    return apps[0] if apps else None


def run_command(command):
    # Keep the capture helper and inference process in one group. Let them
    # release DMA/UIO mappings before unloading the firmware on interruption.
    with subprocess.Popen(command, cwd=ROOT, start_new_session=True) as child:
        try:
            return child.wait()
        except BaseException:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.wait()
            raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--driver', type=Path, required=True, help='Matching bulk qn_tile.ko from the existing dependency package')
    p.add_argument('--evidence', type=Path, required=True)
    p.add_argument('--app', choices=('asr-v3-bulk', 'asr-v3-mic-select'), default='asr-v3-bulk')
    p.add_argument('--hardware-manifest', type=Path, help='Required for the separately built selectable-microphone image')
    p.add_argument('command', nargs=argparse.REMAINDER)
    a = p.parse_args()
    command = a.command[1:] if a.command[:1] == ['--'] else a.command
    if os.geteuid() != 0 or not command or platform.machine() != 'aarch64':
        p.error('Use sudo on the KV260 and supply a command after --')
    a.evidence.parent.mkdir(parents=True, exist_ok=True)
    if a.evidence.exists():
        raise RuntimeError('Evidence file already exists')
    with open('/run/lock/kv260-asr.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        original = active()
        if original != 'k26-starter-kits' or Path('/sys/module/qn_tile').exists():
            raise RuntimeError('Requires idle k26-starter-kits with no qn_tile module')
        pins = json.loads((ROOT/'config/dependencies.json').read_text())['files_sha256']
        firmware = Path('/lib/firmware/xilinx')/a.app
        names = (a.app+'.bit.bin', a.app+'.dtbo', 'shell.json')
        if a.app == 'asr-v3-bulk':
            expected = {name: pins['firmware/asr-v3-bulk/'+name] for name in names}
        else:
            if a.hardware_manifest is None:
                raise RuntimeError('Selectable microphone firmware requires its build manifest')
            manifest = json.loads(a.hardware_manifest.read_text())
            if (manifest.get('format_version') != 1 or manifest.get('app') != a.app or
                    manifest.get('clock_hz') != 100000000 or manifest.get('protocol') != 'bulk' or
                    set(manifest.get('files_sha256', {})) != set(names)):
                raise RuntimeError('Invalid selectable-microphone build manifest')
            expected = manifest['files_sha256']
        for name in names:
            if sha(firmware/name) != expected[name]:
                raise RuntimeError('Installed firmware changed: '+name)
        driver_key = 'drivers/prebuilt/'+platform.release()+'/bulk/qn_tile.ko'
        if sha(a.driver) != pins.get(driver_key):
            raise RuntimeError('Bulk driver differs from the qualified dependency')
        if execute('modinfo', '-F', 'vermagic', str(a.driver)).split()[0] != platform.release():
            raise RuntimeError('Driver kernel mismatch')
        record = {'original_app': original, 'app': a.app, 'firmware_sha256': expected,
                  'driver_sha256': sha(a.driver), 'restored': False, 'command': command}
        def stop(signum, frame):
            raise KeyboardInterrupt('Signal '+str(signum))
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        status = 1
        try:
            execute('xmutil', 'unloadapp')
            execute('xmutil', 'loadapp', a.app)
            clock = Path('/sys/bus/platform/devices')/(a.app+'-clock')/'set_rate'
            clock.write_text('100000000')
            execute('insmod', str(a.driver.resolve()))
            if Path('/sys/module/qn_tile/parameters/bulk_protocol').read_text().strip() != '1':
                raise RuntimeError('Bulk protocol unavailable')
            if not Path('/dev/qn-tile').is_char_device():
                raise RuntimeError('DMA device unavailable')
            record['clock_hz'] = int(clock.read_text())
            if abs(record['clock_hz'] - 100000000) > 5:
                raise RuntimeError('Unexpected bulk clock')
            try:
                status = run_command(command)
            except KeyboardInterrupt:
                status = 130
            record['command_exit_code'] = status
        finally:
            try:
                if Path('/sys/module/qn_tile').exists():
                    execute('rmmod', 'qn_tile')
                if active() != original:
                    if active() is not None:
                        execute('xmutil', 'unloadapp')
                    execute('xmutil', 'loadapp', original)
                record['restored'] = (active() == original and not Path('/sys/module/qn_tile').exists()
                    and Path('/sys/class/fpga_manager/fpga0/state').read_text().strip() == 'operating')
                if not record['restored']:
                    raise RuntimeError('Board restoration failed')
            finally:
                a.evidence.write_text(json.dumps(record, indent=2)+'\n')
        raise SystemExit(status)


if __name__ == '__main__':
    main()
