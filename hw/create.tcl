# Recreated on every invocation; no generated project files are source artifacts.
set root [file normalize [file join [file dirname [info script]] ..]]
set design [lindex $argv 0]
if {$design ni {loopback fe full full150}} {error "Unknown design $design"}
set out $root/build/hw/$design
create_project -force stt $out/project -part xck26-sfvc784-2LV-c
set_property board_part xilinx.com:kv260_som:part0:1.4 [current_project]
set_param general.maxThreads 6
if {$design in {fe full full150}} { add_files [glob $root/rtl/frontend/*.sv]; add_files [glob $root/rtl/frontend/*.mem] }
if {$design in {full full150}} { add_files [glob $root/rtl/engine/*.sv] }
create_bd_design system
create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:* ps
apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e -config {apply_board_preset "1"} [get_bd_cells ps]
set_property -dict [list CONFIG.PSU__FPGA_PL0_ENABLE 1 CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ 100 CONFIG.PSU__USE__M_AXI_GP0 1 CONFIG.PSU__USE__M_AXI_GP1 0 CONFIG.PSU__USE__S_AXI_GP2 1 CONFIG.PSU__SAXIGP2__DATA_WIDTH 128] [get_bd_cells ps]
if {$design eq "full150"} {
 set_property -dict {CONFIG.PSU__FPGA_PL1_ENABLE 1 CONFIG.PSU__CRL_APB__PL1_REF_CTRL__FREQMHZ 150} [get_bd_cells ps]
 # KV260 Ubuntu boots with a 1.5 GHz IOPLL. The board preset models 1 GHz,
 # which silently rounds a 150 MHz request to 142.856 MHz. Match the existing
 # board PLL and use /15 and /10; these PS init settings are not executed by
 # overlay deployment (the overlay changes only the two PL output rates).
 source $root/hw/full150_ps.tcl
 create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:* engine_rst
 connect_bd_net [get_bd_pins ps/pl_clk1] [get_bd_pins engine_rst/slowest_sync_clk]
 connect_bd_net [get_bd_pins ps/pl_resetn0] [get_bd_pins engine_rst/ext_reset_in]
}
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:* rst
connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_pins rst/slowest_sync_clk] [get_bd_pins ps/maxihpm0_fpd_aclk]
connect_bd_net [get_bd_pins ps/pl_resetn0] [get_bd_pins rst/ext_reset_in]
if {$design eq "full150"} {set hp_clock pl_clk1} else {set hp_clock pl_clk0}
connect_bd_net [get_bd_pins ps/$hp_clock] [get_bd_pins ps/saxihp0_fpd_aclk]
create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:* ctrl
set_property -dict [list CONFIG.NUM_SI 1 CONFIG.NUM_MI [expr {$design in {full full150} ? 7 : ($design eq "fe" ? 5 : 3)}]] [get_bd_cells ctrl]
connect_bd_intf_net [get_bd_intf_pins ps/M_AXI_HPM0_FPD] [get_bd_intf_pins ctrl/S00_AXI]
create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:* mem
set_property -dict [list CONFIG.NUM_SI [expr {$design in {full full150} ? 7 : ($design eq "fe" ? 5 : 2)}] CONFIG.NUM_MI 1] [get_bd_cells mem]
connect_bd_intf_net [get_bd_intf_pins mem/M00_AXI] [get_bd_intf_pins ps/S_AXI_HP0_FPD]
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat:* irqs
set_property CONFIG.NUM_PORTS [expr {$design in {full full150} ? 8 : ($design eq "fe" ? 6 : 3)}] [get_bd_cells irqs]
connect_bd_net [get_bd_pins irqs/dout] [get_bd_pins ps/pl_ps_irq0]
source $root/hw/platform.tcl
add_dma dma M00_AXI S00_AXI S01_AXI In0 In1 0xA0000000
create_bd_cell -type ip -vlnv xilinx.com:ip:axis_data_fifo:* fifo
set_property -dict {CONFIG.TDATA_NUM_BYTES 16 CONFIG.HAS_TKEEP 1 CONFIG.HAS_TLAST 1 CONFIG.FIFO_DEPTH 512} [get_bd_cells fifo]
connect_bd_intf_net [get_bd_intf_pins dma/M_AXIS_MM2S] [get_bd_intf_pins fifo/S_AXIS]
connect_bd_intf_net [get_bd_intf_pins fifo/M_AXIS] [get_bd_intf_pins dma/S_AXIS_S2MM]
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:* gpio
set_property -dict {CONFIG.C_GPIO_WIDTH 1 CONFIG.C_ALL_OUTPUTS 1 CONFIG.C_IS_DUAL 1 CONFIG.C_GPIO2_WIDTH 1 CONFIG.C_ALL_INPUTS_2 1} [get_bd_cells gpio]
if {$design eq "loopback"} {
create_bd_port -dir O pmod_led_d1
create_bd_port -dir I pmod_btn_sw1
connect_bd_net [get_bd_ports pmod_led_d1] [get_bd_pins gpio/gpio_io_o]
connect_bd_net [get_bd_ports pmod_btn_sw1] [get_bd_pins gpio/gpio2_io_i]
}
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_timer:* timer
set_property CONFIG.enable_timer2 0 [get_bd_cells timer]
connect_bd_net [get_bd_pins timer/interrupt] [get_bd_pins irqs/In2]
foreach {cell port} {gpio M01_AXI timer M02_AXI} {
 connect_bd_intf_net [get_bd_intf_pins ctrl/$port] [get_bd_intf_pins $cell/S_AXI]
 connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_pins $cell/s_axi_aclk]
 connect_bd_net [get_bd_pins rst/peripheral_aresetn] [get_bd_pins $cell/s_axi_aresetn]
}
foreach c {ctrl mem} {
 if {$design eq "full150"} {
  set_property CONFIG.NUM_CLKS 2 [get_bd_cells $c]
  # Primary clock follows the PS-side interface; endpoint clocks select CDC.
  if {$c eq "mem"} {set primary pl_clk1; set secondary pl_clk0; set reset engine_rst} else {set primary pl_clk0; set secondary pl_clk1; set reset rst}
  connect_bd_net [get_bd_pins ps/$primary] [get_bd_pins $c/aclk]
  connect_bd_net [get_bd_pins ps/$secondary] [get_bd_pins $c/aclk1]
  connect_bd_net [get_bd_pins $reset/interconnect_aresetn] [get_bd_pins $c/aresetn]
  continue
 }
 connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_pins $c/aclk]
 connect_bd_net [get_bd_pins rst/interconnect_aresetn] [get_bd_pins $c/aresetn]
}
connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_pins fifo/s_axis_aclk]
connect_bd_net [get_bd_pins rst/peripheral_aresetn] [get_bd_pins fifo/s_axis_aresetn]
assign_bd_address -offset 0xA0010000 -range 64K -target_address_space [get_bd_addr_spaces ps/Data] [get_bd_addr_segs gpio/S_AXI/Reg]
assign_bd_address -offset 0xA0020000 -range 64K -target_address_space [get_bd_addr_spaces ps/Data] [get_bd_addr_segs timer/S_AXI/Reg]
if {$design in {fe full full150}} {
 add_rtl_pipeline frontend stt_frontend M03_AXI M04_AXI S02_AXI S03_AXI In3 In4 In5 0xA0030000 0xA0040000 32 s_axis_samples m_axis_features S04_AXI
 # GPIO channel 1 (UIO stt-fe-reset) is an active-high reset of stt_frontend only,
 # so each clip restarts framing and sequence numbers without resetting the DMA.
 create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:* fe_rst
 set_property CONFIG.C_AUX_RESET_HIGH 1 [get_bd_cells fe_rst]
 connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_pins fe_rst/slowest_sync_clk]
 connect_bd_net [get_bd_pins ps/pl_resetn0] [get_bd_pins fe_rst/ext_reset_in]
 connect_bd_net [get_bd_pins gpio/gpio_io_o] [get_bd_pins fe_rst/aux_reset_in]
 disconnect_bd_net [get_bd_nets -of_objects [get_bd_pins frontend/aresetn]] [get_bd_pins frontend/aresetn]
 connect_bd_net [get_bd_pins fe_rst/peripheral_aresetn] [get_bd_pins frontend/aresetn]
 foreach p {pmod_i2s_bclk pmod_i2s_lrclk pmod_i2s_dout pmod_i2s2_bclk pmod_i2s2_lrclk pmod_i2s2_dout pmod_led_d1 pmod_btn_sw1} {
  create_bd_port -dir [get_property DIR [get_bd_pins frontend/$p]] $p
  connect_bd_net [get_bd_ports $p] [get_bd_pins frontend/$p]
 }
 connect_bd_net [get_bd_ports pmod_btn_sw1] [get_bd_pins gpio/gpio2_io_i]
}
if {$design in {full full150}} {
 set_property CONFIG.PSU__USE__IRQ1 1 [get_bd_cells ps]
 # PL IRQ0 carries SPI 89..96; IRQ1[0] carries SPI 104.
 create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat:* engine_rx_irq
 set_property CONFIG.NUM_PORTS 1 [get_bd_cells engine_rx_irq]
 connect_bd_net [get_bd_pins engine_rx_irq/dout] [get_bd_pins ps/pl_ps_irq1]
 # RX IRQ uses a separate concat.
 create_bd_cell -type module -reference stt_engine engine
 set engine_clk ps/pl_clk0
 set engine_reset rst/peripheral_aresetn
 if {$design eq "full150"} {set engine_clk ps/pl_clk1; set engine_reset engine_rst/peripheral_aresetn}
 add_dma engine_dma M06_AXI S05_AXI S06_AXI In7 engine_rx_irq/In0 0xA0060000 128 "" $engine_clk $engine_reset
 connect_bd_intf_net [get_bd_intf_pins ctrl/M05_AXI] [get_bd_intf_pins engine/s_axil]
 connect_bd_intf_net [get_bd_intf_pins engine_dma/M_AXIS_MM2S] [get_bd_intf_pins engine/s_axis]
 connect_bd_intf_net [get_bd_intf_pins engine/m_axis] [get_bd_intf_pins engine_dma/S_AXIS_S2MM]
 connect_bd_net [get_bd_pins $engine_clk] [get_bd_pins engine/aclk]
 connect_bd_net [get_bd_pins $engine_reset] [get_bd_pins engine/aresetn]
 connect_bd_net [get_bd_pins engine/irq] [get_bd_pins irqs/In6]
 assign_bd_address -offset 0xA0050000 -range 64K -target_address_space [get_bd_addr_spaces ps/Data] [get_bd_addr_segs -of_objects [get_bd_intf_pins engine/s_axil]]
}
assign_bd_address
validate_bd_design
if {$design eq "full150"} {
 foreach {clock expected} {pl_clk0 100000000 pl_clk1 150000000} {
  set actual [get_property CONFIG.FREQ_HZ [get_bd_pins ps/$clock]]
  if {abs($actual - $expected) > 1000} {error "$clock realized $actual Hz, expected $expected"}
 }
}
save_bd_design
make_wrapper -files [get_files system.bd] -top
add_files $out/project/stt.gen/sources_1/bd/system/hdl/system_wrapper.v
set_property top system_wrapper [current_fileset]
# Copy only constraints whose named port exists in this design.
set f [open $root/rtl/kria-constraints.xdc r]
set x [open $out/ports.xdc w]
foreach line [split [read $f] \n] {
 if {[regexp {get_ports (\w+)} $line -> port] && ($port in {pmod_led_d1 pmod_btn_sw1} || ($design in {fe full full150} && $port in {pmod_i2s_bclk pmod_i2s_lrclk pmod_i2s_dout pmod_i2s2_bclk pmod_i2s2_lrclk pmod_i2s2_dout})) && ![string match #* [string trim $line]]} {puts $x $line}
}
close $f
# PMOD I/O carries 1 MHz I2S, LED and button: inputs are two-flop synchronized in
# stt_frontend and BCLK/WS are register outputs, so no I/O timing budget applies.
if {$design in {fe full full150}} {
 puts $x {set_false_path -from [get_ports {pmod_i2s_dout pmod_i2s2_dout pmod_btn_sw1}]}
 puts $x {set_false_path -to [get_ports {pmod_i2s_bclk pmod_i2s_lrclk pmod_i2s2_bclk pmod_i2s2_lrclk pmod_led_d1}]}
}
close $x
add_files -fileset constrs_1 $out/ports.xdc
launch_runs synth_1 -jobs 6
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "Synthesis failed"}
if {$design eq "full"} {
 set_property STEPS.ROUTE_DESIGN.TCL.POST $root/hw/full_timing.tcl [get_runs impl_1]
}
if {$design eq "full150"} {set_property STEPS.ROUTE_DESIGN.TCL.POST $root/hw/full150_timing.tcl [get_runs impl_1]}
launch_runs impl_1 -to_step write_bitstream -jobs 6
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {error "Implementation failed"}
open_run impl_1
report_timing_summary -file $out/timing.rpt
report_utilization -file $out/utilization.rpt
if {$design eq "full150"} {source $root/hw/report_full150.tcl}
set paths [get_timing_paths -max_paths 1]
if {![llength $paths] || [get_property SLACK $paths] < 0} {error "Timing failed"}
set hold_paths [get_timing_paths -delay_type min -max_paths 1]
if {![llength $hold_paths] || [get_property SLACK $hold_paths] < 0} {error "Hold timing failed"}
file copy -force $out/project/stt.runs/impl_1/system_wrapper.bit $out/stt-$design.bit
write_hw_platform -fixed -include_bit -force -file $out/stt-$design.xsa
