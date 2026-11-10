#!/bin/bash
set -euo pipefail
backend=$1; tag=$2; shift 2
base=/home/ubuntu/stt/t2
exec 9>/home/ubuntu/stt/board.lock
flock -n 9 || { echo 'Board busy'; exit 75; }
echo 'LOCK_ACQUIRED board.lock'
date -u --iso-8601=seconds
for name in sttcap sttcap-cpu fetool i2 full150_probe; do
  if pgrep -x "$name"; then echo "BUSY: $name"; exit 75; fi
done
cd "$base/src"
if test "$backend" = pl; then
  apps=$(xmutil listapps)
  if ! echo "$apps" | awk '$NF=="stt-full150" && /0->/ {found=1} END {exit !found}'; then
    # Refuse to displace another project's firmware.
    if echo "$apps" | awk '/0->/ && $NF !~ /^stt-/ {found=1} END {exit !found}'; then
      echo 'BUSY: another project firmware is loaded'; exit 75
    fi
    if echo "$apps" | grep -q '0->'; then xmutil unloadapp; fi
    xmutil loadapp stt-full150
  fi
  if ! test -d /sys/class/u-dma-buf/udmabuf0; then sh tools/setup-buffers.sh; fi
  for b in 0 1 2 3; do test -c "/dev/udmabuf$b"; done
fi
if test "$backend" = cpu && ! test -x build/sw/app/sttcap-cpu; then
  mkdir -p build/sw/app; cd sw/app
  cc -O2 -mcpu=cortex-a53 -std=c11 -Wall -Wextra -Werror -ffp-contract=off -DCPU_ONLY \
    main.c backend.c stream.c display.c ../qnrt/qnrt.c ../qnrt/wav.c ../frontend_c/frontend.c \
    -pthread -lm -o ../../build/sw/app/sttcap-cpu
fi
findmnt -T "$base" -o TARGET,SOURCE,FSTYPE
python3 "$base/demo_board.py" "$backend" "$tag" "$@"
echo 'PASS replay completed and evidence saved; releasing board.lock'
