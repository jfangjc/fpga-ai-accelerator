"""Check source distribution paths, private-data exclusions and file sizes."""
from pathlib import Path
import argparse
import json
import re

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-only',action='store_true',help='Require external assets to be absent from the distribution')
    args=parser.parse_args()
    errors=[]
    external=json.loads((ROOT/'config/dependencies.json').read_text())['files_sha256']
    sources=set(json.loads((ROOT/'config/assets.json').read_text())['files_sha256'])
    root_files={'.gitattributes','.gitignore','README.md','requirements.txt','config/assets.json'}
    docs={'docs/'+name+'.md' for name in ('architecture','board_setup','build','dependencies','drivers')}
    committed=[]
    if args.source_only:
        errors.extend(name+': external file present in source distribution' for name in external if (ROOT/name).exists())
    for path in ROOT.rglob('*'):
        relative=path.relative_to(ROOT)
        if any(part in {'.git','build','.venv','__pycache__','.Xil'} for part in relative.parts):
            continue
        if relative.parts[0] in {'reports','evidence'} or not path.is_file():
            continue
        if relative.as_posix() in external:
            continue
        name=relative.as_posix()
        if name not in sources|root_files|docs and not (relative.parts[0]=='tests' and path.match('test_*.py')):
            errors.append(name+': not a required source, configuration, test or documentation file')
        committed.append(path)
        if relative.parts[:2]==('.github','workflows'):
            errors.append(str(relative)+': GitHub Actions workflow is excluded')
        if path.stat().st_size>=100*1024*1024:
            errors.append(str(relative)+': exceeds GitHub single-file limit')
        if path.suffix.lower() in {'.wav','.flac','.ppk','.pem','.key'} or path.name.startswith('.env'):
            errors.append(str(relative)+': private or local file type')
        if path.suffix in {'.py','.sh','.md','.json','.tcl','.c','.cc','.bd','.xml','.xci'} and path.name!='check_distribution.py':
            text=path.read_text(encoding='utf-8',errors='replace')
            if re.search(r'C:[/\\]Users[/\\]|/home/ubuntu/|192\.168\.\d+\.\d+|1234567890',text):
                errors.append(str(relative)+': machine-specific path or credential')
    if (ROOT/'.git').exists():
        # Existing Git checkouts are expected after publication. Never initialize one here.
        pass
    if errors: raise SystemExit('\n'.join(errors))
    print('PASS: distribution contains no oversized files, recordings or machine-specific credentials')
    print('Commit candidates:',len(committed),'files,',sum(p.stat().st_size for p in committed),'bytes; evidence and reports excluded')


if __name__=='__main__': main()
