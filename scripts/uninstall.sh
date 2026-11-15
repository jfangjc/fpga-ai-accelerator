#!/bin/bash
# Remove the appliance: sudo /opt/stt-caption/uninstall.sh [--purge]
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "uninstall.sh: run as root" >&2; exit 1; }
systemctl disable --now stt-caption 2>/dev/null || true
rm -f /etc/systemd/system/stt-caption.service
systemctl daemon-reload
rm -rf /opt/stt-caption
if [ "${1:-}" = --purge ]; then
    rm -rf /etc/default/stt-caption /var/lib/stt-caption /lib/firmware/xilinx/stt-kv260
fi
echo "removed stt-caption"
