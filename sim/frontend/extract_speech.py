from pathlib import Path
import tarfile
p=Path('data/dev-clean.tar.gz')
try:
 with tarfile.open(p) as t:
  n=0
  for m in t:
   if m.name.endswith('.flac'):
    out=Path('build/sim/frontend/speech')/Path(m.name).name;out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(t.extractfile(m).read());print(out);n+=1
    if n==3:break
except (OSError,EOFError) as e:print(e)
