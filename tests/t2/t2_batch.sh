#!/bin/bash
# Run with nohup as root; this lock covers soak and all 80 paired T1 runs.
set -euo pipefail
base=/home/ubuntu/stt/t2
exec 9>/home/ubuntu/stt/board.lock
flock -n 9 || exit 75
for name in sttcap fetool i2 full150_probe; do
  if pgrep -x "$name"; then echo "BUSY: $name"; exit 75; fi
done
mkdir -p "$base/soak" "$base/t1"
cd "$base/soak"
date -u --iso-8601=seconds > start.txt
python3 "$base/t2_soak.py" 1800
echo 'SOAK COMPLETE'
# Reuse the accepted T1 script verbatim apart from root/output paths.
sed -e 's|root=/home/ubuntu/stt/t1|root=/home/ubuntu/stt/t2|' \
    -e 's|out=$root/run|out=$root/t1|' "$base/src/tests/t1/t1_batch.sh" | tr -d '\r' > "$base/t1.lf.sh"
bash "$base/t1.lf.sh"
date -u --iso-8601=seconds > "$base/complete.txt"
