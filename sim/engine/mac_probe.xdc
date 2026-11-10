create_clock -name aclk -period 10 [get_ports clk]
set_property HD.CLK_SRC BUFGCE_X0Y0 [get_ports clk]
