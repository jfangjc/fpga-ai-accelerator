"""Install the two FPGA apps and optionally activate the bulk rollback app."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
from board import BULK, BRANCH, driver, preflight, state
from integrity import ROOT, sha, verify_assets


def activate_bulk(root=ROOT):
    if Path('/sys/module/qn_tile').exists():
        subprocess.run(['rmmod','qn_tile'],check=True)
    subprocess.run(['xmutil','unloadapp'],check=False)
    subprocess.run(['xmutil','loadapp',BULK],check=True)
    (Path('/sys/bus/platform/devices')/(BULK+'-clock')/'set_rate').write_text('100000000')
    subprocess.run(['insmod',str(driver(root))],check=True)
    return state(root)


def install_apps(root=ROOT, firmware_root=Path('/lib/firmware/xilinx')):
    for app in (BULK,BRANCH):
        source=root/'firmware'/app
        destination=firmware_root/app
        names=(app+'.bit.bin',app+'.dtbo','shell.json')
        if destination.exists():
            if any(not (destination/name).is_file() or sha(source/name)!=sha(destination/name) for name in names):
                raise RuntimeError('Existing FPGA app differs. Preserve it under another name first: '+str(destination))
            continue
        firmware_root.mkdir(parents=True,exist_ok=True)
        destination.mkdir()
        for name in names:
            shutil.copyfile(source/name,destination/name)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--activate-bulk',action='store_true',help='Replace the active PL app with the shipped bulk image')
    args=parser.parse_args()
    if os.geteuid()!=0:
        parser.error('Run with sudo and the project .venv/bin/python')
    verify_assets()
    # Check both kernel modules before writing firmware or changing the PL app.
    import platform
    for branch in (False,True):
        path=driver(ROOT,branch)
        if not path.is_file():
            raise RuntimeError('No prebuilt module for '+platform.release()+'. See docs/drivers.md')
        if subprocess.check_output(['modinfo','-F','vermagic',str(path)],text=True).split()[0]!=platform.release():
            raise RuntimeError('Kernel mismatch: '+str(path))
    install_apps()
    preflight(ROOT)
    if args.activate_bulk:
        result=activate_bulk()
        target=ROOT/'reports/local/bootstrap.json'
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(result,indent=2)+'\n')
        print('PASS: bulk app, kernel driver, clock and device verified')
    else:
        print('PASS: firmware installed. Use --activate-bulk to set the starting state.')


if __name__=='__main__':
    main()
