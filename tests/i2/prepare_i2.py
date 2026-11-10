"""Prepare board I2 assets from the main checkout's immutable saved vectors."""
import json
import pathlib
import sys

import numpy as np

main = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
base = main / 'data/export'
sys.path.insert(0, str(main / 'model'))
from qnc2_export import export

(out / 'program.qnp2').write_bytes(export((base / 'quartznet_int8.bin').read_bytes()))
manifest = json.loads((base / 'vectors/manifest.json').read_text())
first = json.loads((base / 'layers.json').read_text())[0]['name']
clips = [v for v in manifest if v['layer'] == first]
rows = []
for i in range(10):
    v = clips[i % len(clips)]
    inp = v['inputs'][0]
    a = np.frombuffer((base / 'vectors' / inp['file']).read_bytes(), np.int8).reshape(inp['shape'])
    if i == 0:
        start, t, valid = 0, a.shape[1], inp['valid_frames']
    elif i in (1, 2, 3):
        v = clips[0]
        inp = v['inputs'][0]
        a = np.frombuffer((base / 'vectors' / inp['file']).read_bytes(), np.int8).reshape(inp['shape'])
        start, t, valid = (i - 1) * 160, 304, 300
    else:
        t = min(48, a.shape[1] // 2)
        start = min((i - 4) * 4, a.shape[1] - t)
        valid = max(0, min(t, inp['valid_frames'] - start))
    name = f'window{i}.i8'
    (out / name).write_bytes(np.ascontiguousarray(a[:, start:start+t]).tobytes())
    rows.append(f'/home/ubuntu/stt/i2/{name}\t{t}\t{valid}')
    print(i, v['clip'], start, t, valid)
# Largest RUN_NETWORK window (ENGDRV_NETWORK_FRAMES) and the first length
# past it, which AUTO must send through the per-layer graph.
for t in (640, 656):
    inp = clips[0]['inputs'][0]
    a = np.frombuffer((base / 'vectors' / inp['file']).read_bytes(), np.int8).reshape(inp['shape'])
    name = f'edge{t}.i8'
    (out / name).write_bytes(np.ascontiguousarray(a[:, :t]).tobytes())
    rows.append(f'/home/ubuntu/stt/i2/{name}	{t}	{t}')
    print(name, t, t)
for count in (2, 3):
    inp = clips[0]['inputs'][0]
    a = np.frombuffer((base / 'vectors' / inp['file']).read_bytes(), np.int8).reshape(inp['shape'])
    valid = inp['valid_frames']
    a = np.tile(a[:, :valid], count)
    total_valid = valid * count
    t = (total_valid + 15) // 16 * 16
    if t > 2048:
        continue
    a = np.pad(a, ((0, 0), (0, t-total_valid)))
    name = f'long{count}.i8'
    (out / name).write_bytes(np.ascontiguousarray(a).tobytes())
    rows.append(f'/home/ubuntu/stt/i2/{name}\t{t}\t{total_valid}')
    print(name, t, total_valid)
(out / 'chunks.tsv').write_text('\n'.join(rows) + '\n')
