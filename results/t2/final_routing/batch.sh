#!/bin/bash
# Run with sudo, detached. Never reload firmware or stop unrelated processes.
set -euo pipefail
base=/home/ubuntu/stt/t2/final_routing
exec 9>/home/ubuntu/stt/board.lock
flock -n 9 || exit 75
for name in sttcap fetool i2 full150_probe; do
  if pgrep -x "$name"; then echo "BUSY: $name"; exit 75; fi
done
date -u --iso-8601=seconds
xmutil listapps > "$base/firmware.txt"
grep '0->0,.*stt-full150' "$base/firmware.txt"
sha256sum "$base/src/sw/app/sttcap" /home/ubuntu/stt/i2/program.qnp2 > "$base/assets.txt"
mkdir -p "$base/smoke" "$base/t1" "$base/soak"
cd "$base/smoke"
FINAL_THREADS=3 DUMP_FINALS=1 python3 ../soak.py 24
python3 ../analyze_t2.py soak . > analysis.txt
"$base/src/sw/app/check_final_backends" dump /home/ubuntu/stt/cpu/quartznet_int8.bin /home/ubuntu/stt/i2/program.qnp2 > byte-check.txt 2>&1
python3 - <<'PY'
import json
from pathlib import Path
r=json.load(open('analysis.json'))
assert r['zero_loss_pass'] and r['tcp_missing_events']==0
assert r['invalid_preview_latencies']==0 and r['merged']==0
before=Path('../smoke-pl/dump'); after=Path('dump')
assert sorted(p.name for p in before.glob('final-*'))==sorted(p.name for p in after.glob('final-*'))
assert all(p.read_bytes()==(after/p.name).read_bytes() for p in before.glob('final-*'))
assert any('capture_clock' in s and 'fitted=1' in s for s in open('soak.stderr'))
PY
echo 'SMOKE PASS; exact inputs/logits equal PL'
cd "$base/t1"
while read -r clip; do
  clip=${clip%$'\r'}
  test -n "$clip" || continue
  mkdir -p "$clip.dump"
  "$base/src/sw/app/sttcap" --source pl-inject "/home/ubuntu/stt/cpu/clips/$clip.wav" \
    --blob /home/ubuntu/stt/cpu/quartznet_int8.bin --prior /home/ubuntu/stt/cpu/running_prior.json \
    --threads 3 --backend pl --program /home/ubuntu/stt/i2/program.qnp2 \
    --final-backend auto --final-threads 3 --dump "$clip.dump" \
    --no-ppm --log "$clip.pl.jsonl" --stages "$clip.pl.stages.csv" \
    > "$clip.pl.stdout" 2> "$clip.pl.stderr"
  echo "T1 PASS $clip"
done < "$base/src/model/testset_40.txt"
date -u --iso-8601=seconds > "$base/t1/complete.txt"
echo 'T1 COMPLETE'
cd "$base/soak"
date -u --iso-8601=seconds > start.txt
FINAL_THREADS=3 DUMP_FINALS=1 python3 ../soak.py 1800
python3 ../analyze_t2.py soak . > analysis.txt
python3 ../verify.py . ../original-finals.json > verification.txt
"$base/src/sw/app/check_final_backends" dump /home/ubuntu/stt/cpu/quartznet_int8.bin /home/ubuntu/stt/i2/program.qnp2 > byte-check.txt 2>&1
sha256sum command.json playlist.json soak.jsonl tcp.jsonl monitor.jsonl power.jsonl soak.stages.csv soak.stderr run-result.json dump/* > raw-sha256.txt
date -u --iso-8601=seconds > "$base/complete.txt"
echo 'FINAL ROUTING COMPLETE; stt-full150 remains loaded'
