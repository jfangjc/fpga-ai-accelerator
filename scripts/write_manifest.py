"""Record file hashes after an explicit asset or source update."""
import json
from pathlib import Path
from integrity import ROOT, sha


def main():
    files=[]
    external=json.loads((ROOT/'config/dependencies.json').read_text())['files_sha256']
    for folder in ('software', 'rtl', 'sim', 'scripts', 'hardware'):
        for path in (ROOT/folder).rglob('*'):
            if not path.is_file() or '__pycache__' in path.parts:
                continue
            if path.relative_to(ROOT).as_posix() in external:
                continue
            if path.suffix in ('.py','.cc','.c','.h','.sh','.v','.sv','.tcl','.xdc'):
                files.append(path)
    for path in (ROOT/'config').glob('policies.json'):
        files.append(path)
    files.append(ROOT/'config/dependencies.json')
    data={'format_version':1,'tested_kernel':'6.8.0-1036-xilinx',
          'hardware_clock_hz':166666666,
          'files_sha256':{p.relative_to(ROOT).as_posix():sha(p) for p in sorted(files)}}
    (ROOT/'config/assets.json').write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8',newline='\n')
    print('Wrote',len(files),'asset/source hashes. This does not qualify changed hardware or arithmetic.')


if __name__=='__main__':
    main()
