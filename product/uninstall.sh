#!/bin/bash
# Remove the stt-caption-cpu (CPU baseline) appliance (run as root). Session logs and
# /etc/default/stt-caption-cpu are kept unless --purge is given. The stt-full150
# firmware is left in place: other stt overlays may share the firmware directory.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo 'uninstall.sh: run as root' >&2; exit 1; }
systemctl disable --now stt-caption-cpu 2>/dev/null || true
for v in /sys/class/vtconsole/vtcon*; do grep -q 'frame buffer' "$v/name" && echo 1 > "$v/bind" || true; done
rm -f /etc/systemd/system/stt-caption-cpu.service
systemctl daemon-reload
rm -rf /opt/stt-caption-cpu
if [ "${1:-}" = --purge ]; then rm -rf /var/lib/stt-caption-cpu /etc/default/stt-caption-cpu; fi
echo 'stt-caption-cpu removed'
