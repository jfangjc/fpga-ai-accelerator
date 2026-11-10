#!/bin/bash
# Board side of tests/mic/mic_acceptance.ps1 (run as root): run the appliance
# control script in the foreground for a fixed time, without the service.
#   mic_session.sh CONTROL_SCRIPT SECONDS EVIDENCE_DIR
set -euo pipefail
ctl=$1; seconds=$2; evidence=$3
sessions=/var/lib/stt-caption/sessions
if systemctl is-active -q stt-caption; then echo 'BUSY: stt-caption service is running'; exit 75; fi
old=$(readlink -f "$sessions/latest" 2>/dev/null || true)
bash "$ctl" prepare
bash "$ctl" run >/dev/null 2>&1 &   # events and stderr are kept in the session directory
pid=$!
for _ in $(seq 600); do
  cur=$(readlink -f "$sessions/latest" 2>/dev/null || true)
  [ -n "$cur" ] && [ "$cur" != "$old" ] && grep -q '"Listening"' "$cur/session.jsonl" 2>/dev/null && break
  sleep 0.1
done
echo "READY $(date -u +%Y-%m-%dT%H:%M:%S.%NZ)"
# Board input power (INA260) over the session, sampled every 100 ms.
power=$(mktemp); sh /home/ubuntu/stt/power.sh "$((seconds*10-10))" 0.1 > "$power" 2>&1 &
sleep "$seconds"
pkill -INT -x sttcap || true
rc=0; wait $pid || rc=$?
bash "$ctl" cleanup
echo "sttcap rc=$rc"
d=$(readlink -f "$sessions/latest")
rm -rf "$evidence"; mkdir -p "$evidence"; cp "$d"/* "$evidence/"
wait; mv "$power" "$evidence/power.txt"; cat "$evidence/power.txt"
journalctl -k -b --since "-$((seconds+120)) s" --no-pager | grep -i -E 'error|fail|dma' > "$evidence/kernel.txt" || true
chmod -R a+rX "$evidence"
tail -n 3 "$evidence/stderr.txt"
