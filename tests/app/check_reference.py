"""Compare offline CPU timelines to supplied Python reference files."""
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tests/t1'))
from bench_app import compare
for timeline in sys.argv[1:]:
    path=Path(timeline)
    clip=path.stem if path.stem!='measurements-cpu' else '1089-134691-0023'
    result=compare([json.loads(s) for s in path.read_text().splitlines()],
                   json.loads((ROOT/f'data/export/stream_ref/{clip}.json').read_text()))
    print(clip,json.dumps(result))
    if result['different_updates']:
        raise SystemExit(1)
