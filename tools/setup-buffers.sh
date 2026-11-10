#!/bin/sh
set -eu
cd /home/ubuntu/stt
rev=2cdfd21bb3decb7be02ccf7b5d21c33c8b92b5b0
test -d u-dma-buf || git clone https://github.com/ikwzm/udmabuf.git u-dma-buf
cd u-dma-buf
git checkout "$rev"
make -j4
test -d /sys/class/u-dma-buf/udmabuf0 || insmod ./u-dma-buf.ko udmabuf0=16777216 udmabuf1=16777216 udmabuf2=33554432 udmabuf3=1048576
# Cacheable mmap (do not open O_SYNC); explicit synchronization required.
for b in 0 1 2 3; do
 test -d /sys/class/u-dma-buf/udmabuf$b || { echo "Missing udmabuf$b: stop all DMA owners and reload u-dma-buf with all four allocations" >&2; exit 1; }
 echo 1 > /sys/class/u-dma-buf/udmabuf$b/sync_mode
 echo 0 > /sys/class/u-dma-buf/udmabuf$b/sync_offset
 cat /sys/class/u-dma-buf/udmabuf$b/size > /sys/class/u-dma-buf/udmabuf$b/sync_size
 echo 0 > /sys/class/u-dma-buf/udmabuf$b/sync_direction
done
