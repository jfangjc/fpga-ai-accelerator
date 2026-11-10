"""Build the stt-caption release tarball (run from the repository root; Windows or Linux).

    python tools/product_release.py FIRMWARE_DIR [TAG]

FIRMWARE_DIR holds the validated stt-full150 overlay (bit.bin, dtbo, shell.json).
Every binary asset is checked against the hashes validated on the board. The
last line printed is the tarball's path. Standard library only.
"""
import hashlib
import io
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

VALIDATED = {  # path relative to the repository or FIRMWARE_DIR -> SHA-256
    'data/export/quartznet_int8.bin': '7126b03deb7d3715acd66de69f1c6605461f32c961b53c2fc905137c7142a6ec',
    'data/export/running_prior.json': '66a608be12f041c8cefa175e27df4ffbc6b503efa06289c679260ef355f55a41',
    'data/export/quartznet_qnc2.bin': 'ee07f475801fddca55fd93c2a1fe61f18557626ffc11cee6991f41d78ae9f583',
    'stt-full150.bit.bin': '4cd65023c6c2933fd7704fb373b7428450c8ec37dfd90b690e12a66245adc1d2',
    'stt-full150.dtbo': 'eae42493bb781e4c9b8a65d8ca6556f6d36c16ec35a74c7565c8d291a00d3081',
    'shell.json': '4b7eb77be43fea924276958c69fd918d6b1cabf7f225d6e703c9dd17c19ad8c0',
}
SOURCES = ['sw/app', 'sw/qnrt', 'sw/frontend_c', 'sw/fedrv', 'sw/plio', 'sw/engdrv']
SKIP_SUFFIXES = ('.o', '.a', '.so', '.json', '.jsonl', '.png', '.ppm', '.tar', '.csv')
PRODUCT = ['stt-caption', 'stt-caption.service', 'stt-caption.conf', 'install.sh', 'uninstall.sh']
EXECUTABLE = {'stt-caption', 'install.sh', 'uninstall.sh'}


def git(*args):
    return subprocess.run(['git', *args], capture_output=True, text=True, check=True).stdout.strip()


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check(path, digest):
    if sha256(path) != digest:
        sys.exit(f'unexpected asset: {path}')


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    fw = Path(sys.argv[1])
    tag = sys.argv[2] if len(sys.argv) == 3 else f"{time.strftime('%Y%m%d')}-{git('rev-parse', '--short', 'HEAD')}"
    name = f'stt-caption-{tag}'
    out = Path('build/release') / name
    shutil.rmtree(out, ignore_errors=True)
    for d in ('assets', 'firmware/stt-full150', 'pc'):
        (out / d).mkdir(parents=True)

    for path, digest in VALIDATED.items():
        check(path if path.startswith('data/') else fw / path, digest)
    for f in ('quartznet_int8.bin', 'running_prior.json'):
        shutil.copyfile(f'data/export/{f}', out / 'assets' / f)
    shutil.copyfile('data/export/quartznet_qnc2.bin', out / 'assets/program.qnp2')
    for f in ('stt-full150.bit.bin', 'stt-full150.dtbo', 'shell.json'):
        shutil.copyfile(fw / f, out / 'firmware/stt-full150' / f)

    # Source: tracked application/driver files (no binaries or measurement data).
    files = [f for f in git('ls-files', *SOURCES).splitlines()
             if not f.endswith(SKIP_SUFFIXES) and '/results/' not in f and '/measurements' not in f]
    files += ['sw/app/policy.json', 'model/stream_policy.py']
    with tarfile.open(out / 'source.tar', 'w', format=tarfile.GNU_FORMAT) as tar:
        for f in files:
            tar.add(f, arcname=f, recursive=False, filter=owner(0o644))
    for f in PRODUCT:
        (out / f).write_bytes(Path('product', f).read_bytes().replace(b'\r', b''))
    shutil.copyfile('tools/caption_client.py', out / 'pc/caption_client.py')
    shutil.copyfile('tools/caption_live.py', out / 'pc/caption_live.py')
    shutil.copyfile('docs/product.md', out / 'README.md')
    dirty = subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', 'sw', 'product', 'model', 'tools', 'docs'],
                           stderr=subprocess.DEVNULL).returncode  # git's warnings would stop PowerShell
    (out / 'VERSION').write_text(f"{tag} (git {git('rev-parse', '--short', 'HEAD')}{'-dirty' if dirty else ''})\n", newline='\n')
    sums = [f'{sha256(p)}  {p.relative_to(out).as_posix()}' for p in sorted(out.rglob('*'))
            if p.is_file() and p.name not in ('SHA256SUMS', 'README.md') and p.relative_to(out).parts[0] != 'pc']
    (out / 'SHA256SUMS').write_text('\n'.join(sums) + '\n', newline='\n')

    tarball = Path('build/release') / f'{name}.tar.gz'
    with tarfile.open(tarball, 'w:gz', format=tarfile.GNU_FORMAT) as tar:
        tar.add(out, arcname=name, filter=owner(None))
    print(tarball.as_posix())


def owner(file_mode):
    """Tar filter: root ownership and explicit modes (Windows has no execute bit)."""
    def apply(info):
        if file_mode is not None:
            info.mode = file_mode
        else:
            info.mode = 0o755 if info.isdir() or Path(info.name).name in EXECUTABLE else 0o644
        info.uid = info.gid = 0
        info.uname = info.gname = 'root'
        return info
    return apply


if __name__ == '__main__':
    main()
