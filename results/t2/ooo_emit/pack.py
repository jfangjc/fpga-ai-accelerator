"""Archive completed evidence only; never run during a timing measurement."""
import hashlib
import json
from pathlib import Path
import tarfile

base=Path(__file__).resolve().parent
assert (base/'complete.txt').exists()
digest=lambda p:hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
assets=[base/'src/sw/app/sttcap',Path('/home/ubuntu/stt/i2/program.qnp2'),
        Path('/home/ubuntu/stt/cpu/quartznet_int8.bin'),Path('/home/ubuntu/stt/cpu/running_prior.json')]
(base/'asset-identities.json').write_text(json.dumps({str(p):dict(bytes=p.stat().st_size,sha256=digest(p))
    for p in assets},indent=2)+'\n')
ppm={name:digest(p) for name,p in [('ooo',base/'soak/caption.ppm'),
    ('pl',Path('/home/ubuntu/stt/t2/followup/soak/caption.ppm')),
    ('ordered_auto',Path('/home/ubuntu/stt/t2/final_routing/soak/caption.ppm'))]}
assert len(set(ppm.values()))==1
(base/'ppm-equivalence.json').write_text(json.dumps(ppm,indent=2)+'\n')
raw=[]
for run in ('soak','smoke','smoke-auto1','smoke-auto2','smoke-auto3','smoke-pl'):
    raw += [base/run/name for name in ('soak.jsonl','soak.stdout','tcp.jsonl','caption.ppm')]
raw += [p for p in (base/'t1').glob('*') if p.suffix in ('.jsonl','.stdout','.stderr','.csv')]
assert all(p.exists() for p in raw)
manifest={str(p.relative_to(base)):dict(bytes=p.stat().st_size,sha256=digest(p)) for p in raw}
(base/'raw-files.json').write_text(json.dumps(manifest,indent=2)+'\n')
with tarfile.open(base/'raw.tar.gz','w:gz',compresslevel=3) as archive:
    for p in raw: archive.add(p,arcname=str(p.relative_to(base)))
small=[p for p in base.glob('*') if p.is_file() and p.suffix in ('.txt','.log','.json')]
for run in ('soak','smoke','smoke-auto1','smoke-auto2','smoke-auto3','smoke-pl'):
    small += [p for p in (base/run).glob('*') if p.is_file() and
              p.name not in ('soak.jsonl','soak.stdout','tcp.jsonl','soak.wav')]
small += [base/'t1/complete.txt']
small += [base/'pilot-upgrade/run-result.json']
small += [base/'smoke-upgrade/analysis.json']
with tarfile.open(base/'evidence.tar.gz','w:gz') as archive:
    for p in small: archive.add(p,arcname=str(p.relative_to(base)))
print(json.dumps({p.name:dict(bytes=p.stat().st_size,sha256=digest(p)) for p in
    (base/'raw.tar.gz',base/'evidence.tar.gz')},indent=2))
