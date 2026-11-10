#!/bin/sh
# Usage: sh power.sh [samples=100] [interval_seconds=0.1]
set -eu
n=${1:-100}; interval=${2:-0.1}
# Select board input power sensor explicitly if more than one exists.
sensor=${STT_POWER_INPUT:-}
if [ -z "$sensor" ]; then
 for h in /sys/class/hwmon/hwmon*; do
  case "$(cat "$h/name")" in ina260*) sensor="$h/power1_input"; break;; esac
 done
fi
[ -r "$sensor" ] || { echo 'Set STT_POWER_INPUT to board input power*_input (microwatts)' >&2; exit 1; }
{ i=0; while [ "$i" -lt "$n" ]; do cat "$sensor"; sleep "$interval"; i=$((i+1)); done; } | awk '{w=$1/1000000;s+=w;if(w>m)m=w} END {printf "samples=%d mean_W=%.6f max_W=%.6f\n",NR,s/NR,m}'

