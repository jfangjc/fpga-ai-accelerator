"""Exact physical INT32 conformance before quantized model inference."""
import argparse
import json
from pathlib import Path
import sys
import time
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'software'))
from moonshine.matrix import BulkDevice, Projection


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--library', default='build/native/libqnc_moonshine.so')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    device = BulkDevice(a.library)
    rows = []
    rng = np.random.default_rng(260)
    try:
        for m, k, t in [(1, 1, 1), (17, 31, 19), (512, 620, 25), (620, 512, 16), (1024, 1024, 33)]:
            w = rng.integers(-127, 128, (m, k), dtype=np.int8)
            x = rng.integers(-127, 128, (k, t), dtype=np.int8)
            start = time.perf_counter()
            y = device.multiply(w, x)
            seconds = time.perf_counter() - start
            expected = w.astype(np.int32) @ x.astype(np.int32)
            np.testing.assert_array_equal(y, expected)
            packet = device.prepare(w)
            loads_before = device.stats()['loads']
            start = time.perf_counter()
            np.testing.assert_array_equal(device.multiply(packet, x), expected)
            prepared_seconds = time.perf_counter()-start
            loads = device.stats()['loads']
            if loads != loads_before+1:
                raise AssertionError('Prepared load was not counted')
            np.testing.assert_array_equal(device.multiply(packet, x), expected)
            if device.stats()['loads'] != loads:
                raise AssertionError('Resident prepared matrix was unnecessarily reloaded')
            rows.append({'m': m, 'k': k, 't': t, 'seconds': seconds,
                         'prepared_seconds': prepared_seconds, 'exact': True, 'prepared_exact': True})
        # Both dimensions exceed a hardware tile; verify scaling and accumulation too.
        w = rng.normal(size=(1240, 2480)).astype(np.float32)
        x = rng.normal(size=(1, 17, 2480)).astype(np.float32)
        np.testing.assert_array_equal(Projection(w, device=device)(x), Projection(w)(x))
        result = {'passed': True, 'raw_tiles': rows, 'large_projection_exact': True, 'physical': device.stats()}
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result, indent=2))
    finally:
        device.close()


if __name__ == '__main__':
    main()
