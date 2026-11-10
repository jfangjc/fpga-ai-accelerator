"""Package all pointwise manifest vectors without recomputing their oracle."""
import json
from pathlib import Path
import struct
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'model'))
from qnc2_export import read_qnw
from golden_int8 import requant


def main():
    records = read_qnw((ROOT / 'data/export/quartznet_int8.bin').read_bytes())
    names = json.loads((ROOT / 'data/export/layers.json').read_text())
    lookup = {x['name']: records[x['id']-1] for x in names}
    directory = ROOT / 'data/export/vectors'
    cases = []
    for entry in json.loads((directory / 'manifest.json').read_text()):
        d, arrays = lookup[entry['layer']]
        if d[0] != 1 or d[6] != 1 or d[10] != 1:
            continue
        assert d[7:10] == [1, 1, 0], entry['layer']
        source, target = entry['inputs'][0], entry['output']
        ci, t = source['shape']
        co = target['shape'][0]
        assert ci <= 1024 and co <= 1024 and t <= 800
        tiled = np.frombuffer(arrays[0], np.int8).reshape(-1, (ci+15)//16, 16, 32)
        weights = tiled.transpose(0, 3, 1, 2).reshape(-1, ((ci+15)//16)*16)[:co, :ci].copy()
        cases.append((f"{entry['clip']}/{entry['layer']}", ci, co, t,
                      source['valid_frames'], source['dtype'] == 'int8', d[3],
                      arrays[1:], weights.tobytes(),
                      (directory/source['file']).read_bytes(),
                      (directory/target['file']).read_bytes(), False))
    golden_count = len(cases)
    # Pointwise outputs are independent across time: a 150-frame prefix of a
    # longer golden vector is also a golden vector (with the clipped mask).
    seen = set()
    for case in list(cases):
        name, ci, co, t, valid, signed, relu, params, w, x, y, overflow = case
        layer = name.split('/', 1)[1]
        if layer not in seen and t >= 150:
            seen.add(layer)
            xp = np.frombuffer(x, np.uint8).reshape(ci, t)[:, :150].copy()
            yp = np.frombuffer(y, np.uint8).reshape(co, t)[:, :150].copy()
            cases.append((f'T150/{layer}', ci, co, 150, min(valid, 150), signed,
                          relu, params, w, xp.tobytes(), yp.tobytes(), overflow))
    assert len(seen) == 94, 'expected a T150 prefix for every pointwise layer'
    prefix_count = len(seen)
    rng = np.random.default_rng(41)
    for ci, co, t, valid in [(1, 1, 1, 1), (19, 29, 17, 11), (17, 33, 31, 0), (3, 29, 7, 6)]:
        for signed in (False, True):
            for relu in (False, True):
                x = rng.integers(-128 if signed else 0, 128 if signed else 256,
                                 (ci, t), dtype=np.int16)
                w = rng.integers(-127, 128, (co, ci), dtype=np.int16)
                b = rng.integers(-1000, 1000, co, dtype=np.int32)
                m = np.full(co, 1 << 30, np.int32)
                s = np.full(co, 7, np.int32)
                masked = x.copy(); masked[:, valid:] = 0
                acc = w.astype(np.int64) @ masked.astype(np.int64) + b[:, None]
                y = requant(acc, m, s, 0 if relu else -128, 255 if relu else 127)
                cases.append((f'synthetic/{ci}/{co}/{t}/{signed}/{relu}', ci, co, t,
                              valid, signed, relu, [z.tobytes() for z in (b, m, s)],
                              w.astype(np.int8).tobytes(), x.astype(np.int8 if signed else np.uint8).tobytes(),
                              y.astype(np.uint8 if relu else np.int8).tobytes(), False))
    # Overflow must remain latched even if a later term brings the sum in range.
    for b, w, x, signed in [(2147483647, [1, -1], [1, 1], True),
                            (-2147483648, [-1, 1], [255, 255], False)]:
        cases.append(('overflow', 2, 1, 1, 1, signed, False,
                      [struct.pack('<i', z) for z in (b, 1 << 30, 0)],
                      np.array(w, np.int8).tobytes(), bytes(x), bytes(1), True))
    with open('layers.bin', 'wb') as f:
        f.write(struct.pack('<I', len(cases)))
        for name, ci, co, t, valid, signed, relu, params, w, x, y, overflow in cases:
            label = name.encode()
            f.write(struct.pack('<9I', ci, co, t, valid, signed, relu, len(label), overflow, 0))
            f.write(label)
            for p in params: f.write(p)
            f.write(w); f.write(x); f.write(y)
    print(f'Packaged {golden_count} manifest pointwise vectors + {prefix_count} T150 prefixes + {len(cases)-golden_count-prefix_count} synthetic cases')


if __name__ == '__main__':
    main()
