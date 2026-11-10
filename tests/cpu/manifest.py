import json,pathlib,sys
root=pathlib.Path(sys.argv[1] if len(sys.argv)>1 else 'data/export')
ids={x['name']:x['id']-1 for x in json.loads((root/'layers.json').read_text())}
rows=[]
for e in json.loads((root/'vectors/manifest.json').read_text()):
 a=e['inputs'][0];o=e['output'];b='vectors/'+e['inputs'][1]['file'] if len(e['inputs'])>1 else '-'
 rows.append(f"{ids[e['layer']]} {a['shape'][0]} {o['shape'][0]} {a['shape'][1]} {a['valid_frames']} {o['shape'][1]} {o['valid_frames']} vectors/{a['file']} {b} vectors/{o['file']}")
(root/'vectors.tsv').write_text('\n'.join(rows)+'\n')
