# Lightweight elaboration check for the module-reference helper. No implementation.
set root [file normalize [file join [file dirname [info script]] ..]]
create_project -force check $root/build/hw/interface-check -part xck26-sfvc784-2LV-c
add_files [glob $root/rtl/frontend/*.sv]
add_files [glob $root/rtl/frontend/*.mem]
create_bd_design check
create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:* ps
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:* rst
foreach c {ctrl mem} { create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:* $c }
set_property -dict {CONFIG.NUM_SI 1 CONFIG.NUM_MI 2} [get_bd_cells ctrl]
set_property -dict {CONFIG.NUM_SI 2 CONFIG.NUM_MI 1} [get_bd_cells mem]
set_property CONFIG.PSU__USE__M_AXI_GP0 1 [get_bd_cells ps]
connect_bd_intf_net [get_bd_intf_pins ps/M_AXI_HPM0_FPD] [get_bd_intf_pins ctrl/S00_AXI]
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat:* irqs
set_property CONFIG.NUM_PORTS 3 [get_bd_cells irqs]
source $root/hw/platform.tcl
add_rtl_pipeline frontend stt_frontend M00_AXI M01_AXI S00_AXI S01_AXI In0 In1 In2 0xA0030000 0xA0040000 32 s_axis_samples m_axis_features
puts "PASS module-reference helper with stt_frontend 32-bit streams"
