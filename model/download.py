"""Reproducible public model and LibriSpeech download; no NeMo needed."""
import argparse, hashlib, pathlib, tarfile, urllib.request

MODEL_URL = 'https://api.ngc.nvidia.com/v2/models/nvidia/nemo/stt_en_quartznet15x5/versions/1.0.0rc1/files/stt_en_quartznet15x5.nemo'
MODEL_SHA256 = '1b9b7b87a9277e6fef164d8f99d1226f0511af154423bbf919b920421ac9602f'
def download(url, path):
    path = pathlib.Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        tmp = path.with_suffix('.partial'); urllib.request.urlretrieve(url, tmp); tmp.replace(path)
    print(path, hashlib.sha256(path.read_bytes()).hexdigest(), flush=True)
    return path

if __name__ == '__main__':
    p=argparse.ArgumentParser(); p.add_argument('--datasets', action='store_true'); a=p.parse_args()
    checkpoint=download(MODEL_URL, 'data/quartznet.nemo')
    if hashlib.sha256(checkpoint.read_bytes()).hexdigest()!=MODEL_SHA256:
        raise ValueError('NVIDIA checkpoint hash differs from validated archive')
    if a.datasets:
        for split in ('test-clean','dev-clean'):
            archive=download('https://www.openslr.org/resources/12/'+split+'.tar.gz', 'data/'+split+'.tar.gz')
            with tarfile.open(archive) as t: t.extractall('data', filter='data')
