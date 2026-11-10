"""Compare RSS/size of mappings in the task's read-only process snapshots."""
import json
from pathlib import Path
import re
import sys

folder=Path(sys.argv[1])
def read(path):
    result={}; current=None
    for line in path.read_text().splitlines():
        m=re.match(r'^([0-9a-f]+-[0-9a-f]+) (\S+) \S+ \S+ \S+\s*(.*)$',line)
        if m:
            current=dict(address=m[1],permissions=m[2],name=m[3] or '[anonymous]')
            result[m[1].split('-')[0]]=current
        elif current is not None and ':' in line:
            key,value=line.split(':',1)
            if key in ('Size','Rss','Private_Dirty','Anonymous'):
                current[key]=int(value.split()[0])
    return result
before,after=read(folder/'early.smaps'),read(folder/'late.smaps')
changed=[]
for address in sorted(before.keys()|after.keys()):
    a,b=before.get(address,{}),after.get(address,{})
    delta={k:b.get(k,0)-a.get(k,0) for k in ('Size','Rss','Private_Dirty','Anonymous')}
    if any(delta.values()): changed.append(dict(mapping=b or a,before=a,delta_kib=delta))
result=dict(before_rss_kib=sum(r['Rss'] for r in before.values()),
            after_rss_kib=sum(r['Rss'] for r in after.values()),
            before_size_kib=sum(r['Size'] for r in before.values()),
            after_size_kib=sum(r['Size'] for r in after.values()),changes=changed)
(folder/'comparison.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
