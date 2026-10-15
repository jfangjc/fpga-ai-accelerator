"""Read and validate the KV260 FPGA application, driver and DMA IRQ state."""
import json
import platform
from pathlib import Path
import re
import subprocess
from integrity import sha, verify_assets

BRANCH='asr-v3-branch512-ab166'
BULK='asr-v3-bulk'


def irqs():
    result={}
    for line in Path('/proc/interrupts').read_text().splitlines():
        if line.strip().endswith('xilinx-dma-controller'):
            number=re.match(r'\s*(\d+):',line).group(1)
            directory=Path('/proc/irq')/number
            result[number]={'requested':(directory/'smp_affinity_list').read_text().strip(),
                            'effective':(directory/'effective_affinity_list').read_text().strip()}
    if len(result)!=2:
        raise RuntimeError('Expected exactly two PL DMA IRQs')
    return result


def configure_irqs(core):
    if core not in (0,1,2,3):
        raise ValueError('IRQ core must be 0, 1, 2 or 3')
    for number in irqs():
        (Path('/proc/irq')/number/'smp_affinity_list').write_text(str(core))
    if any(v['effective']!=str(core) for v in irqs().values()):
        raise RuntimeError('IRQ placement did not take effect')


def driver(root, branch=False):
    return root/'drivers/prebuilt'/platform.release()/('branch' if branch else 'bulk')/'qn_tile.ko'


def preflight(root):
    verify_assets(root)
    if platform.machine()!='aarch64':
        raise RuntimeError('Physical inference requires an ARM64 KV260')
    for branch in (False,True):
        path=driver(root,branch)
        if not path.is_file():
            raise RuntimeError('No driver for this kernel. See docs/drivers.md')
        version=subprocess.check_output(['modinfo','-F','vermagic',str(path)],text=True).split()[0]
        if version!=platform.release():
            raise RuntimeError('Kernel module mismatch: '+str(path))
    for app in (BULK,BRANCH):
        for name in (app+'.bit.bin',app+'.dtbo','shell.json'):
            if sha(root/'firmware'/app/name)!=sha(Path('/lib/firmware/xilinx')/app/name):
                raise RuntimeError('Installed firmware mismatch: '+app+'/'+name)


def state(root, branch=False):
    app=BRANCH if branch else BULK
    module=driver(root,branch)
    expected=subprocess.check_output(['modinfo','-F','srcversion',str(module)],text=True).strip()
    if Path('/sys/module/qn_tile/srcversion').read_text().strip()!=expected:
        raise RuntimeError('Loaded module differs from the shipped compatible driver')
    if Path('/sys/class/fpga_manager/fpga0/state').read_text().strip()!='operating':
        raise RuntimeError('FPGA manager is not operating')
    parameter='residual_branch_protocol' if branch else 'bulk_protocol'
    if (Path('/sys/module/qn_tile/parameters')/parameter).read_text().strip()!='1':
        raise RuntimeError('Wrong qn_tile protocol')
    rate=int((Path('/sys/bus/platform/devices')/(app+'-clock')/'set_rate').read_text())
    if abs(rate-(166666666 if branch else 100000000))>5:
        raise RuntimeError('Unexpected PL clock: '+str(rate))
    if not Path('/dev/qn-tile').is_char_device():
        raise RuntimeError('Missing /dev/qn-tile')
    return {'app':app,'kernel':platform.release(),'clock_readback_hz':rate,
            'module_srcversion':expected,'driver_sha256':sha(module),'irq':irqs()}


def check_active_branch(root):
    return state(root,True)
