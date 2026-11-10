#!/bin/bash
# Detached invocation; one lock covers every timing run and native rebuild.
set -euo pipefail
base=/home/ubuntu/stt/t2/ooo_emit
exec 9>/home/ubuntu/stt/board.lock
flock 9
cd "$base/src"
make -C sw/app CFLAGS='-O2 -mcpu=cortex-a53 -std=c11 -Wall -Wextra -Werror -ffp-contract=off' all check > "$base/build.txt" 2>&1
sha256sum sw/app/sttcap > "$base/assets.txt"
sha256sum sw/app/main.c sw/app/caption.h sw/app/display.c sw/app/display.h > "$base/deployed-source-sha256.txt"
cd "$base"
xmutil listapps > acceptance-firmware.txt
grep '0->0,.*stt-full150' acceptance-firmware.txt
mkdir -p smoke soak t1
cd smoke
FINAL_THREADS=3 python3 ../soak.py 24
python3 ../analyze_t2.py soak . > analysis.txt
python3 - <<'PY'
import json,sys
from pathlib import Path
sys.path.insert(0,'..')
from verify_caption import verify_caption
events=[json.loads(s) for s in Path('soak.jsonl').open()]
result=verify_caption(events)
r=json.load(open('analysis.json'))
assert r['zero_loss_pass'] and not r['merged'] and not r['tcp_missing_events']
assert result['previews_before_earlier_final']>0
snapshot=json.load(open('late-join.json'))
assert snapshot['event'].pop('snapshot')
assert snapshot['event'] in events
assert snapshot['latency_ms']<50
result['late_join_pass']=True
Path('verification.json').write_text(json.dumps(result,indent=2)+'\n')
PY
cd ../soak
date -u --iso-8601=seconds > start.txt
FINAL_THREADS=3 python3 ../soak.py 1800
python3 ../analyze_t2.py soak . > analysis.txt
python3 ../verify.py . /home/ubuntu/stt/t2/followup/original-finals.json > verification.txt
sha256sum command.json playlist.json soak.jsonl tcp.jsonl monitor.jsonl power.jsonl soak.stages.csv soak.stderr run-result.json soak.wav > raw-sha256.txt
echo 'SOAK COMPLETE'
cd ../t1
while read -r clip; do
  clip=${clip%$'\r'}
  test -n "$clip" || continue
  "$base/src/sw/app/sttcap" --source pl-inject "/home/ubuntu/stt/cpu/clips/$clip.wav" \
    --blob /home/ubuntu/stt/cpu/quartznet_int8.bin --prior /home/ubuntu/stt/cpu/running_prior.json \
    --threads 3 --backend pl --program /home/ubuntu/stt/i2/program.qnp2 \
    --final-backend auto --final-threads 3 \
    --no-ppm --log "$clip.pl.jsonl" --stages "$clip.pl.stages.csv" \
    > "$clip.pl.stdout" 2> "$clip.pl.stderr"
  echo "T1 PASS $clip"
done < "$base/src/model/testset_40.txt"
date -u --iso-8601=seconds > complete.txt
cd ..
xmutil listapps > final-board.txt
date -u --iso-8601=seconds > complete.txt
echo 'OOO ACCEPTANCE COMPLETE; stt-full150 remains loaded'
