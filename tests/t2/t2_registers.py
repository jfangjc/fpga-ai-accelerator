"""Read FE UI/mic registers; run only with the board lock and no capture owner."""
import json
import mmap
from pathlib import Path
import struct
import sys

uio = next(p.parent.name for p in Path('/sys/class/uio').glob('uio*/name')
           if p.read_text().strip()=='stt-frontend')
clear = '--clear-button' in sys.argv
with open('/dev/'+uio, 'r+b' if clear else 'rb', buffering=0) as f:
    with mmap.mmap(f.fileno(),4096,flags=mmap.MAP_SHARED,
                   prot=mmap.PROT_READ | (mmap.PROT_WRITE if clear else 0)) as m:
        def reg(o):
            return struct.unpack_from('<I',m,o)[0]
        result = dict(control=hex(reg(0)), status_before=hex(reg(4)),
            sticky_before=bool(reg(4)&8), frames=reg(16), mic_overruns=reg(20))
        for mic, offset in ((1,64),(2,80)):
            peak, dc, lo, hi = struct.unpack_from('<IiII',m,offset)
            result[f'mic{mic}'] = dict(peak=peak,dc=dc,mean_square=lo+(hi<<32))
        if clear:
            struct.pack_into('<I',m,8,8)
        result.update(clear_button_written=clear,status_after=hex(reg(4)),sticky_after=bool(reg(4)&8))
        print(json.dumps(result))
