"""Attribute resident-page changes between two Linux smaps snapshots."""
import json
from pathlib import Path
import re
import sys

def read(path):
    result={}
    for line in Path(path).read_text().splitlines():
        if re.match(r'^[0-9a-f]+-[0-9a-f]+ ',line):
            key=line
            result[key]={}
        elif ':' in line:
            field, value=line.split(':',1)
            if field in ('Size','Rss','Private_Dirty','Private_Clean'):
                result[key][field]=int(value.split()[0])
    return result

a,b=map(read,sys.argv[1:])
rows=[]
for key in sorted(a.keys() | b.keys()):
    before,after=a.get(key,{}),b.get(key,{})
    delta=after.get('Rss',0)-before.get('Rss',0)
    if delta:
        rows.append(dict(mapping=key,before=before,after=after,rss_delta_kib=delta))
print(json.dumps(dict(rss_delta_kib=sum(r['rss_delta_kib'] for r in rows),changes=rows),indent=2))
