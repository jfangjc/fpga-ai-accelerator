"""Offline capture-format/comparator regression; run with the model virtualenv."""
import contextlib
import io
import tempfile
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import i1


def main():
    rng = np.random.default_rng(41)
    samples = rng.integers(-12000, 12000, 1120, dtype=np.int16)
    raw, _ = i1.ff.mel_power_fixed(samples)
    q = dict(input_scale=np.float32(.04), running_prior=dict(
        count=10, mean=np.zeros(64), m2=np.ones(64)))
    with tempfile.TemporaryDirectory() as td:
        i1.OUT = Path(td)
        i1.quant = lambda: q
        np.save(i1.OUT / 'synthetic.ref.npy', raw)
        T = len(raw)
        rec = np.zeros(T, i1.REC)
        rec['seq'] = np.arange(T)
        rec['power'] = raw
        features = i1.host_features(raw, q)
        hdr = np.array([0x31494546, T, len(samples), 0, 1, T, 100000000, 0], '<u8')
        path = i1.OUT / 'synthetic.bulk.bin'
        suffix = b''.join(features[m][1].tobytes() for m in ('utterance', 'running'))
        suffix += b''.join(features[m][0].astype('<f4').tobytes() for m in ('utterance', 'running'))
        def write():
            path.write_bytes(hdr.tobytes() + rec.tobytes() + suffix)
        args = SimpleNamespace(files=[str(path)], json=None)
        write()
        i1.compare(args)
        for field in ('power', 'seq'):
            saved = rec.copy()
            if field == 'power':
                rec['power'][2, 7] ^= np.uint64(1)
            else:
                rec['seq'][2] += 1
            write()
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    i1.compare(args)
            except SystemExit as e:
                assert e.code == 1
            else:
                raise AssertionError('corruption accepted: ' + field)
            rec = saved
        print('PASS: synthetic fixed-point capture, power corruption, sequence corruption')


if __name__ == '__main__':
    main()
