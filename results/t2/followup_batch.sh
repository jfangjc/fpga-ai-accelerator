#!/bin/bash
# Native build is prepared separately; never reload the accepted bitstream.
set -euo pipefail
base=/home/ubuntu/stt/t2/followup
exec 9>/home/ubuntu/stt/board.lock
flock 9
for name in sttcap fetool i2 full150_probe; do
  if pgrep -x "$name"; then echo "BUSY: $name"; exit 75; fi
done
date -u --iso-8601=seconds
xmutil listapps > "$base/firmware.txt"
cat /sys/kernel/debug/clk/clk_summary > "$base/clocks.txt"
sha256sum "$base/src/sw/app/sttcap" /home/ubuntu/stt/i2/program.qnp2 > "$base/assets.txt"
sed 's|BASE = Path.*|BASE = Path("/home/ubuntu/stt/t2/followup")|' /home/ubuntu/stt/t2/t2_soak.py > "$base/soak.py"
mkdir -p "$base/smoke" "$base/soak" "$base/t1"
cd "$base/smoke"
python3 "$base/soak.py" 24
python3 "$base/analyze_t2.py" soak . > analysis.txt
python3 - <<'PY'
import json
r=json.load(open('analysis.json'))
assert r['zero_loss_pass'] and r['tcp_missing_events']==0, r
assert r['invalid_preview_latencies']==0, r
assert any('capture_clock' in s and 'fitted=1' in s for s in open('soak.stderr'))
assert sum(1 for _ in open('soak.stages.csv'))==r['events']+1
PY
echo 'SMOKE PASS'
cd "$base/soak"
date -u --iso-8601=seconds > start.txt
python3 "$base/soak.py" 1800
python3 "$base/analyze_t2.py" soak . > analysis.txt
python3 "$base/clock_fit.py" soak.stderr --output clock-fit.json > clock-fit.txt
sha256sum command.json playlist.json soak.jsonl tcp.jsonl monitor.jsonl power.jsonl soak.stages.csv soak.stderr run-result.json > raw-sha256.txt
echo 'SOAK COMPLETE'
cd "$base/t1"
while read -r clip; do
  clip=${clip%$'\r'}
  test -n "$clip" || continue
  "$base/src/sw/app/sttcap" --source pl-inject "/home/ubuntu/stt/cpu/clips/$clip.wav" \
    --blob /home/ubuntu/stt/cpu/quartznet_int8.bin --prior /home/ubuntu/stt/cpu/running_prior.json \
    --threads 3 --backend pl --program /home/ubuntu/stt/i2/program.qnp2 \
    --no-ppm --log "$clip.pl.jsonl" --stages "$clip.pl.stages.csv" \
    > "$clip.pl.stdout" 2> "$clip.pl.stderr"
  echo "T1 PASS $clip"
done < "$base/src/model/testset_40.txt"
date -u --iso-8601=seconds > "$base/complete.txt"
echo 'FOLLOWUP COMPLETE; stt-full150 remains loaded'
