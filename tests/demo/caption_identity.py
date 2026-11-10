"""Compare the caption content of two sttcap session logs (timing fields ignored).

    python tests/demo/caption_identity.py OLD.jsonl NEW.jsonl
"""
import json
import sys

KEYS = ('phrase', 'state', 'preview', 'tentative', 'final', 'caption', 'committed_prefix', 'segments', 'feature_count', 'window_start')


def load(path):
    return [tuple(json.dumps(e.get(k), sort_keys=True) for k in KEYS)
            for e in map(json.loads, open(path)) if not e.get('snapshot')]


a, b = load(sys.argv[1]), load(sys.argv[2])
same = a == b
print(f'{len(a)} vs {len(b)} events; caption content identical: {same}')
if not same:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            print('first difference at event', i, x, y, sep='\n'); break
sys.exit(0 if same else 1)
