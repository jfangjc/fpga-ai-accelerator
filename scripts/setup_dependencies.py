"""Fetch and verify external assets without adding them to the source distribution."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def install_archive(archive, root=ROOT):
    root = root.resolve()
    manifest = json.loads((root / 'config/dependencies.json').read_text())
    if digest(archive) != manifest['archive_sha256']:
        raise ValueError('Dependency archive SHA256 mismatch')
    expected = manifest['files_sha256']
    # Validate the whole archive and every existing destination before writing assets.
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) != len(expected) or {e.filename for e in entries} != set(expected):
            raise ValueError('Dependency archive file list mismatch')
        for entry in entries:
            name = PurePosixPath(entry.filename)
            target = (root / entry.filename).resolve()
            if name.is_absolute() or '..' in name.parts or '\\' in entry.filename or not target.is_relative_to(root):
                raise ValueError('Unsafe dependency path: ' + entry.filename)
            if entry.file_size > 128 * 1024 * 1024:
                raise ValueError('Dependency file is too large')
            if hashlib.sha256(source.read(entry)).hexdigest() != expected[entry.filename]:
                raise ValueError('Dependency file SHA256 mismatch: ' + entry.filename)
            if target.exists() and (not target.is_file() or digest(target) != expected[entry.filename]):
                raise ValueError('Refusing to overwrite changed dependency: ' + entry.filename)
        for entry in entries:
            target = root / entry.filename
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
                temporary = Path(output.name)
                try:
                    with source.open(entry) as stream:
                        shutil.copyfileobj(stream, output)
                except BaseException:
                    output.close()
                    temporary.unlink(missing_ok=True)
                    raise
            try:
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
    print('PASS:', len(expected), 'external files installed with their notices and driver sources')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--archive', type=Path, help='Use a local dependency archive')
    source.add_argument('--url', help='Download the dependency archive over HTTPS')
    args = parser.parse_args()
    if args.archive:
        install_archive(args.archive)
        return
    manifest = json.loads((ROOT / 'config/dependencies.json').read_text())
    url = args.url or os.environ.get('FPGA_AI_DEPENDENCIES_URL') or manifest.get('download_url')
    if not url:
        parser.error('Supply --archive PATH or --url HTTPS_URL. See docs/dependencies.md for publication setup.')
    if not url.startswith('https://'):
        parser.error('Dependency downloads require an HTTPS URL')
    cache = ROOT / 'build/downloads'
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache) as folder:
        archive = Path(folder) / manifest['archive_name']
        request = urllib.request.Request(url, headers={'User-Agent': 'fpga-ai-accelerator-setup/1'})
        with urllib.request.urlopen(request, timeout=60) as response, archive.open('wb') as output:
            if not response.geturl().startswith('https://'):
                raise ValueError('Refusing a download redirected away from HTTPS')
            remaining = manifest['archive_size']
            while block := response.read(min(1024 * 1024, remaining + 1)):
                remaining -= len(block)
                if remaining < 0:
                    raise ValueError('Dependency download exceeds the expected archive size')
                output.write(block)
        install_archive(archive)


if __name__ == '__main__':
    main()
