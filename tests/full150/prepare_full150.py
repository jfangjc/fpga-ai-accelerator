"""Reuse immutable main-checkout I2 vectors and add exact T300 and tiled T1552."""
import pathlib
import runpy
import sys
import numpy as np

main, out = map(pathlib.Path, sys.argv[1:3])
runpy.run_path(str(pathlib.Path(__file__).with_name('prepare_i2.py')), run_name='__main__')
remote = '/home/ubuntu/stt/full150/assets'
rows = (out / 'chunks.tsv').read_text().replace('/home/ubuntu/stt/i2', remote).splitlines()
x = np.frombuffer((out / 'window1.i8').read_bytes(), np.int8).reshape(64, 304)
(out / 'window300.i8').write_bytes(np.ascontiguousarray(x[:, :300]).tobytes())
rows.append(f'{remote}/window300.i8\t300\t300')
full = np.frombuffer((out / 'window0.i8').read_bytes(), np.int8).reshape(64, -1)
long = np.tile(full[:, :774], (1, 3))[:, :1552]
(out / 'window1552.i8').write_bytes(np.ascontiguousarray(long).tobytes())
rows.append(f'{remote}/window1552.i8\t1552\t1552')
(out / 'chunks.tsv').write_text('\n'.join(rows) + '\n')
