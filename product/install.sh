#!/bin/bash
# Install the stt-caption appliance from an unpacked release directory.
# Run as root on the KV260:  sudo ./install.sh [--no-enable] [--no-start]
# Builds sttcap natively with the project's strict flags and runs its unit
# checks, installs firmware, model assets, the u-dma-buf module and the
# systemd service. An existing /etc/default/stt-caption is preserved.
set -euo pipefail
pkg=$(cd "$(dirname "$0")" && pwd)
prefix=/opt/stt-caption
udmabuf_rev=2cdfd21bb3decb7be02ccf7b5d21c33c8b92b5b0
enable=1 start=1
for a; do
  case $a in --no-enable) enable=0 ;; --no-start) start=0 ;; *) echo "usage: $0 [--no-enable] [--no-start]" >&2; exit 2 ;; esac
done
[ "$(id -u)" = 0 ] || { echo 'install.sh: run as root' >&2; exit 1; }
cd "$pkg"
echo '== verify release contents'
sha256sum --quiet -c SHA256SUMS

echo '== native build and unit checks'
build=$pkg/build
rm -rf "$build"; mkdir -p "$build"; tar -xf source.tar -C "$build"
touch "$build/sw/app/policy.h" "$build/sw/app/policy.json"   # generated files are committed
make -s -C "$build/sw/engdrv" BUILD_ROOT="$build/out" CFLAGS='-O3 -mcpu=cortex-a53 -std=c11 -Wall -Wextra -Werror' libengdrv.a
make -s -C "$build/sw/app" BUILD_ROOT="$build/out" CFLAGS='-O2 -mcpu=cortex-a53 -std=c11 -Wall -Wextra -Werror -ffp-contract=off' all check
make -s -C "$build/sw/fedrv" BUILD_ROOT="$build/out" fetool

echo "== u-dma-buf module ($udmabuf_rev) for $(uname -r)"
origin=https://github.com/ikwzm/udmabuf.git
[ -d /home/ubuntu/stt/u-dma-buf/.git ] && origin=/home/ubuntu/stt/u-dma-buf
git clone -q "$origin" "$build/u-dma-buf"
git -C "$build/u-dma-buf" -c advice.detachedHead=false checkout -q "$udmabuf_rev"
(cd "$build/u-dma-buf" && make -s -j4)   # its Makefile uses $PWD

echo "== install to $prefix"
systemctl stop stt-caption 2>/dev/null || true
install -d "$prefix/bin" "$prefix/share" "$prefix/lib" /lib/firmware/xilinx/stt-full150 /var/lib/stt-caption/sessions
install -m 755 "$build/out/sw/app/sttcap" "$build/out/sw/fedrv/fetool" stt-caption "$prefix/bin/"
install -m 644 assets/quartznet_int8.bin assets/running_prior.json assets/program.qnp2 "$prefix/share/"
install -m 644 "$build/u-dma-buf/u-dma-buf.ko" "$prefix/lib/"
install -m 644 firmware/stt-full150/* /lib/firmware/xilinx/stt-full150/
[ -e /etc/default/stt-caption ] || install -m 644 stt-caption.conf /etc/default/stt-caption
install -m 644 stt-caption.service /etc/systemd/system/stt-caption.service
cp SHA256SUMS VERSION "$prefix/"
(cd "$prefix" && sha256sum bin/* share/* lib/* > INSTALLED.sha256)
systemctl daemon-reload
if [ "$enable" = 1 ]; then systemctl enable stt-caption; fi
if [ "$start" = 1 ]; then systemctl restart stt-caption; sleep 12; systemctl --no-pager status stt-caption | head -5; fi
echo "PASS stt-caption $(cat VERSION) installed"
