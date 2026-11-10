#!/bin/sh
set -eu
cd /home/ubuntu/stt/plio
rm -f /home/ubuntu/stt/irq-soak.exit
nohup sh -c 'taskset -c 3 chrt -f 50 ../build/sw/plio/tests/irq_soak 1800 > /home/ubuntu/stt/irq-soak.log 2>&1; echo $? > /home/ubuntu/stt/irq-soak.exit' < /dev/null > /home/ubuntu/stt/irq-launch.log 2>&1 &
echo "soak_pid=$!"
