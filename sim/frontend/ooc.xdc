# Read before synthesis so optimization sees the actual 100 MHz target.
create_clock -name aclk -period 10.000 [get_ports aclk]
