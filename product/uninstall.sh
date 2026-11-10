#!/bin/bash
# Remove the stt-caption appliance (run as root). Firmware files in
# /lib/firmware/xilinx/stt-full150, session logs and /etc/default/stt-caption
# are kept unless --purge is given.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo 'uninstall.sh: run as root' >&2; exit 1; }
systemctl disable --now stt-caption 2>/dev/null || true
for v in /sys/class/vtconsole/vtcon*; do grep -q 'frame buffer' "$v/name" && echo 1 > "$v/bind" || true; done
rm -f /etc/systemd/system/stt-caption.service
systemctl daemon-reload
rm -rf /opt/stt-caption
if [ "${1:-}" = --purge ]; then rm -rf /var/lib/stt-caption /etc/default/stt-caption /lib/firmware/xilinx/stt-full150; fi
echo 'stt-caption removed'
