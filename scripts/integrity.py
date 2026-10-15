"""Check project sources and installed external assets."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            value.update(block)
    return value.hexdigest()


def verify_assets(root=ROOT, source_only=False):
    root = root.resolve()
    manifest = json.loads((root/'config/assets.json').read_text())
    expected_files = dict(manifest['files_sha256'])
    if not source_only and (root/'config/dependencies.json').exists():
        expected_files.update(json.loads((root/'config/dependencies.json').read_text())['files_sha256'])
    for name, expected in expected_files.items():
        target = (root/name).resolve()
        if not target.is_relative_to(root):
            raise ValueError('Asset path escapes project: '+name)
        if not target.is_file() or sha(target) != expected:
            raise ValueError('Asset missing or changed: '+name+'. Run scripts/setup_dependencies.py for external assets.')
    return manifest


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-only',action='store_true',help='Check the repository before installing external assets')
    args=parser.parse_args()
    verify_assets(source_only=args.source_only)
    print('PASS: project sources verified' if args.source_only else 'PASS: project sources and external assets verified')
