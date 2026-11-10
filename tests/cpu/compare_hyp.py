import json,pathlib
r=json.loads(pathlib.Path('data/cpu/results.json').read_text());g=json.loads(pathlib.Path('data/wer_int8.json').read_text());gold={x['id']:x for x in g['clips']}
for x in r['clips']:
 if x['mode']=='utterance' and x['hyp']!=gold[x['id']]['hyp']:print(x['id'],'C:',x['hyp'],'PY:',gold[x['id']]['hyp'])
