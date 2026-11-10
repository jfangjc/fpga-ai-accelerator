#!/bin/bash
# Performance repeat: same measured binary, no synchronous --dump I/O.
set -euo pipefail
base=/home/ubuntu/stt/t2/final_routing
exec 9>/home/ubuntu/stt/board.lock
flock -n 9 || exit 75
for name in sttcap fetool i2 full150_probe; do
  if pgrep -x "$name"; then echo "BUSY: $name"; exit 75; fi
done
test -f "$base/soak-instrumented/byte-check.txt"
grep '^PASS 196 finals,' "$base/soak-instrumented/byte-check.txt"
sha256sum "$base/src/sw/app/sttcap" > "$base/clean-assets.txt"
mkdir -p "$base/soak"
cd "$base/soak"
date -u --iso-8601=seconds > start.txt
FINAL_MODE=auto FINAL_THREADS=3 python3 ../soak.py 1800
python3 ../analyze_t2.py soak . > analysis.txt
python3 ../verify.py . ../original-finals.json > verification.txt
python3 ../report.py . /home/ubuntu/stt/t2/followup/soak > comparison.txt
sha256sum command.json playlist.json soak.jsonl tcp.jsonl monitor.jsonl power.jsonl soak.stages.csv soak.stderr run-result.json soak.wav > raw-sha256.txt
date -u --iso-8601=seconds > "$base/clean-complete.txt"
xmutil listapps > "$base/clean-firmware.txt"
echo 'CLEAN FINAL ROUTING COMPLETE; stt-full150 remains loaded'
