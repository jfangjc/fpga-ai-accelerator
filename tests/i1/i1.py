"""I1 host side: prepare LibriSpeech clips and fixed-point references, then
compare board dumps from `fetool clip` / `fetool soak` bit-exactly.

  python tests/i1/i1.py prepare [--clips 6]      -> data/i1/
  python tests/i1/i1.py compare data/i1/board/<id>.<mode>.bin ...
  python tests/i1/i1.py soakref data/i1/board/soak.ref.bin

References: model/frontend_fixed.py (PL mel power), model/frontend.py
log_normalize (utterance and running-with-dev-clean-prior), and the model's
INT8 input quantiser (float32 divide by input_scale, half-away, clip).
"""
import argparse, json, pickle, sys
from multiprocessing import Pool
from pathlib import Path
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'model'))
import frontend_fixed as ff  # noqa: E402

OUT = ROOT / 'data/i1'
REC = np.dtype([('seq', '<u4'), ('pad', '<u4'), ('power', '<u8', 64),
                ('irq', '<u8'), ('avail', '<u8'), ('pop', '<u8')])


def quant():
    return pickle.load(open(ROOT / 'data/int8.pkl', 'rb'))


def flac(cid):
    spk, chap, _ = cid.split('-')
    return ROOT / f'data/LibriSpeech/test-clean/{spk}/{chap}/{cid}.flac'


def ref_job(args):
    name, x = args
    raw, _ = ff.mel_power_fixed(x)
    np.save(OUT / f'{name}.ref.npy', raw)
    return name, raw.shape[0]


def prepare(a):
    OUT.mkdir(parents=True, exist_ok=True)
    ids = (ROOT / 'model/testset_40.txt').read_text().split()[:a.clips]
    jobs, loop = [], []
    for cid in ids:
        x, sr = sf.read(flac(cid), dtype='int16')
        assert sr == 16000 and x.ndim == 1
        x.astype('<i2').tofile(OUT / f'{cid}.pcm')
        jobs.append((cid, x))
        loop += [x, np.zeros(-len(x) % 160, np.int16)]
    # Soak loop: clips padded to whole hops, then one silent hop, so the
    # stream is periodic (frame k == frame k mod P, preemphasis history 0).
    loop = np.concatenate(loop + [np.zeros(160, np.int16)])
    loop.astype('<i2').tofile(OUT / 'loop.pcm')
    jobs.append(('loop', np.concatenate([loop, loop[:320]])))
    q = quant()
    p = q['running_prior']
    np.concatenate([[float(p['count'])], p['mean'], p['m2']]).astype('<f8').tofile(OUT / 'prior.bin')
    (OUT / 'clips.txt').write_text('\n'.join(ids) + '\n')
    with Pool(len(jobs)) as pool:
        for name, T in pool.imap_unordered(ref_job, jobs):
            print(name, T, 'frames')
    print('loop samples', len(loop), 'period frames', len(loop) // 160, 'input_scale', repr(q['input_scale']))


def host_features(raw, q):
    from frontend import log_normalize
    from golden_int8 import round_away
    mel = raw.astype(np.float64).T * 2.0 ** -42
    p = q['running_prior']
    out = {}
    for mode, state in (('utterance', None), ('running', dict(count=p['count'], mean=p['mean'], m2=p['m2']))):
        f, _ = log_normalize(mel, mode, state)
        out[mode] = (f, np.clip(round_away(f / q['input_scale']), -128, 127).astype(np.int8))
    return out


def compare(a):
    q = quant()
    rows = []
    for path in map(Path, a.files):
        b = path.read_bytes()
        h = np.frombuffer(b, '<u8', 8)
        assert h[0] == 0x31494546, path
        T = int(h[1])
        off = 64
        rec = np.frombuffer(b, REC, T, off); off += T * REC.itemsize
        qu = np.frombuffer(b, np.int8, 64 * T, off).reshape(64, T); off += 64 * T
        qr = np.frombuffer(b, np.int8, 64 * T, off).reshape(64, T); off += 64 * T
        zu = np.frombuffer(b, '<f4', 64 * T, off).reshape(64, T); off += 256 * T
        zr = np.frombuffer(b, '<f4', 64 * T, off).reshape(64, T); off += 256 * T
        assert off == len(b), (path, off, len(b))
        cid = path.name.split('.')[0]
        ref = np.load(OUT / f'{cid}.ref.npy')
        hf = host_features(ref, q)
        diff = rec['power'] != ref if ref.shape == rec['power'].shape else None
        r = dict(run=path.name, frames=T, ref_frames=int(ref.shape[0]), received=int(h[5]),
                 sequence_ok=bool(np.array_equal(rec['seq'], np.arange(T))),
                 band_values=int(ref.size),
                 band_mismatches=int(diff.sum()) if diff is not None else -1,
                 frame_mismatches=int(diff.any(1).sum()) if diff is not None else -1)
        for mode, zb, qb in (('utterance', zu, qu), ('running', zr, qr)):
            f, i8 = hf[mode]
            r[f'{mode}_f32_mismatches'] = int((zb != f).sum())
            r[f'{mode}_int8_mismatches'] = int((qb != i8).sum())
            r[f'{mode}_int8_max_abs'] = int(np.abs(qb.astype(int) - i8).max())
        r['bitexact'] = bool(r['band_mismatches'] == 0 and r['sequence_ok'] and T == ref.shape[0] == h[5])
        rows.append(r)
        print(json.dumps(r))
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=1) + '\n')
    if not all(r['bitexact'] for r in rows):
        raise SystemExit(1)


def soakref(a):
    ref = np.load(OUT / 'loop.ref.npy')
    P = len(np.fromfile(OUT / 'loop.pcm', '<i2')) // 160
    board = np.fromfile(a.file, '<u8').reshape(-1, 64)
    ok = board.shape == (P, 64) and np.array_equal(board, ref[:P])
    print(json.dumps(dict(period_frames=P, board_frames=int(board.shape[0]),
                          band_mismatches=int((board != ref[:P]).sum()) if board.shape == (P, 64) else -1,
                          bitexact=bool(ok))))
    if not ok:
        raise SystemExit(1)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('prepare'); p.add_argument('--clips', type=int, default=6); p.set_defaults(fn=prepare)
    p = sub.add_parser('compare'); p.add_argument('files', nargs='+'); p.add_argument('--json'); p.set_defaults(fn=compare)
    p = sub.add_parser('soakref'); p.add_argument('file'); p.set_defaults(fn=soakref)
    a = ap.parse_args()
    a.fn(a)
