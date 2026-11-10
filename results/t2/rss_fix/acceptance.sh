#!/bin/bash
# Static front-end scratch (no per-phrase heap allocation): strict native build,
# 24 s smoke, 30 min soak, 40-clip T1. Run detached as root from
# ~/stt/t2/rss_fix after staging src/ and the ooo_emit scripts there.
set -euo pipefail
base=/home/ubuntu/stt/t2/rss_fix
exec 9>/home/ubuntu/stt/board.lock
flock 9
cd "$base"
xmutil listapps > firmware.txt
grep '0->0,.*stt-full150' firmware.txt
cd src
make -C sw/engdrv clean
make -C sw/engdrv CFLAGS='-O3 -mcpu=cortex-a53 -std=c11 -Wall -Wextra -Werror' libengdrv.a
make -C sw/app clean
make -C sw/app CFLAGS='-O2 -mcpu=cortex-a53 -std=c11 -Wall -Wextra -Werror -ffp-contract=off' all check > "$base/build.txt" 2>&1
sha256sum sw/app/sttcap sw/app/main.c sw/frontend_c/frontend.c sw/frontend_c/frontend.h > "$base/assets.txt"
cd "$base"
mkdir -p smoke soak t1
cd smoke
FINAL_THREADS=3 python3 ../soak.py 24
python3 ../analyze_t2.py soak . > analysis.txt
cd ../soak
date -u --iso-8601=seconds > start.txt
FINAL_THREADS=3 python3 ../soak.py 1800
python3 ../analyze_t2.py soak . > analysis.txt
python3 ../verify.py . /home/ubuntu/stt/t2/followup/original-finals.json > verification.txt
sha256sum command.json playlist.json soak.jsonl tcp.jsonl monitor.jsonl power.jsonl soak.stages.csv soak.stderr run-result.json > raw-sha256.txt
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
done < "$base/src/model/testset_40.txt"
cd ..
xmutil listapps > final-board.txt
date -u --iso-8601=seconds > complete.txt
echo 'RSS FIX ACCEPTANCE COMPLETE; stt-full150 remains loaded'
