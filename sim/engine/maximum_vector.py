"""Maximum S1 dimensions with a sparse, independently computable oracle."""
import struct
from pathlib import Path
import numpy as np
ci = co = 1024
t = 800
x = (np.arange(ci*t, dtype=np.uint32)*37+13).astype(np.uint8).reshape(ci,t)
w = np.eye(co, ci, dtype=np.int8)
y = ((x.astype(np.uint16)+1)//2).astype(np.uint8)
name = b'maximum/1024/1024/800'
with Path('maximum.bin').open('wb') as f:
    f.write(struct.pack('<I9I', 1, ci, co, t, t, 0, 1, len(name), 0, 0))
    f.write(name)
    for value in (0, 1 << 30, 0):
        f.write(np.full(co,value,dtype='<i4').tobytes())
    f.write(w.tobytes())
    f.write(x.tobytes())
    f.write(y.tobytes())
print('Generated maximum-size S1 case: 1024x1024x800, 819200 expected bytes')
