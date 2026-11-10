#!/bin/bash
# Run the entire batch under flock ~/stt/board.lock (not one lock per clip).
set -euo pipefail
root=/home/ubuntu/stt/t1
app=$root/src/build/sw/app/sttcap
out=$root/run
mkdir -p "$out"
cd "$out"
date -u --iso-8601=seconds > environment.txt
uname -a >> environment.txt
sha256sum "$app" /home/ubuntu/stt/i2/program.qnp2 /home/ubuntu/stt/cpu/quartznet_int8.bin >> environment.txt
for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
  if test -r "$f"; then echo "$f: $(cat "$f")"; else echo "$f: unavailable"; fi
done >> environment.txt
sh /home/ubuntu/stt/power.sh 50 0.1 > power-idle.txt
index=0
while read -r clip; do
  clip=${clip%$'\r'}
  test -n "$clip" || continue
  order='cpu pl'
  if ((index % 2)); then order='pl cpu'; fi
  for backend in $order; do
    tag=$clip.$backend
    wav=/home/ubuntu/stt/cpu/clips/$clip.wav
    n=$(( ($(stat -c %s "$wav") - 44) * 9 / 32000 ))
    sh /home/ubuntu/stt/power.sh "$n" 0.1 > "$tag.power" 2>&1 & power=$!
    "$app" --source pl-inject "$wav" \
      --blob /home/ubuntu/stt/cpu/quartznet_int8.bin \
      --prior /home/ubuntu/stt/cpu/running_prior.json \
      --threads 3 --mel-threshold .01 --backend "$backend" \
      --program /home/ubuntu/stt/i2/program.qnp2 \
      --no-ppm --log "$tag.jsonl" --stages "$tag.stages.csv" \
      > "$tag.stdout" 2> "$tag.stderr" & app_pid=$!
    LC_ALL=C pidstat -h -u -p "$app_pid" 1 > "$tag.pidstat" & monitor=$!
    rc=0; wait "$app_pid" || rc=$?
    kill "$monitor" 2>/dev/null || true
    wait "$monitor" 2>/dev/null || true
    wait "$power"
    echo "$clip $backend exit=$rc"
    test "$rc" = 0
  done
  index=$((index+1))
done < "$root/src/model/testset_40.txt"
echo "PASS paired A/B clips=$index"
