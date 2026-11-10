#!/bin/bash
set -euo pipefail
base=/home/ubuntu/stt/t2/ooo_emit
exec 9>/home/ubuntu/stt/board.lock
flock -n 9 || exit 75
cd "$base"
xmutil listapps > firmware.txt
grep '0->0,.*stt-full150' firmware.txt
tar -xf source.tar -C src
cd src
make -C sw/engdrv clean
make -C sw/engdrv CFLAGS='-O3 -mcpu=cortex-a53 -std=c11 -Wall -Wextra -Werror' libengdrv.a
make -C sw/app clean
make -C sw/app CFLAGS='-O2 -mcpu=cortex-a53 -std=c11 -Wall -Wextra -Werror -ffp-contract=off' all check
sha256sum sw/app/sttcap > "$base/binary.txt"
cd "$base"
for n in 3 2 1; do
  mkdir -p "smoke-auto$n"
  cd "smoke-auto$n"
  FINAL_MODE=auto FINAL_THREADS=$n python3 ../soak.py 24
  python3 ../analyze_t2.py soak . > analysis.txt
  cd ..
done
mkdir -p smoke-pl
cd smoke-pl
FINAL_MODE=pl python3 ../soak.py 24
python3 ../analyze_t2.py soak . > analysis.txt
cd ..
date -u --iso-8601=seconds > smoke-complete.txt
