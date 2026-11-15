#!/bin/bash
# Build and install the appliance on the board: sudo ./install.sh [--enable]
set -euo pipefail
cd "$(dirname "$0")"
prefix=/opt/stt-caption
[ "$(id -u)" = 0 ] || { echo "install.sh: run as root" >&2; exit 1; }

echo "== build"
make -s -C sw BUILD=../build
if [ ! -d build/u-dma-buf ]; then
    git clone -q https://github.com/ikwzm/udmabuf.git build/u-dma-buf
    git -C build/u-dma-buf -c advice.detachedHead=false checkout -q 2cdfd21bb3decb7be02ccf7b5d21c33c8b92b5b0
fi
(cd build/u-dma-buf && make -s -j4)

echo "== install to $prefix"
systemctl stop stt-caption 2>/dev/null || true
install -d "$prefix/bin" "$prefix/share" "$prefix/lib" /lib/firmware/xilinx/stt-kv260 /var/lib/stt-caption/sessions
install -m 755 build/sttcap build/transcribe stt-caption "$prefix/bin/"
install -m 644 quartznet_engine.bin running_prior.json "$prefix/share/"
install -m 644 build/u-dma-buf/u-dma-buf.ko "$prefix/lib/"
install -m 644 stt-kv260/* /lib/firmware/xilinx/stt-kv260/
[ -e /etc/default/stt-caption ] || install -m 644 stt-caption.conf /etc/default/stt-caption
install -m 644 stt-caption.service /etc/systemd/system/
systemctl daemon-reload
if [ "${1:-}" = --enable ]; then
    systemctl enable --now stt-caption
fi
echo "installed stt-caption"
