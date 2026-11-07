# KV260 bitstream and overlay app, built with vivado -mode batch -source hw/build.tcl [-tclargs nomic]
# Writes build/hw/kv260/stt-kv260. nomic leaves out the microphone, LED and button, for evaluation
#   0xA0000000 front end   0xA0010000 front end DMA   0xA0020000 resets (GPIO)   0xA0030000 engine DMA
set root [file normalize [file join [file dirname [info script]] ..]]
set out $root/build/hw/kv260
set bin [file join $::env(XILINX_VIVADO) bin]
set mic [expr {[lsearch $argv nomic] < 0}]
create_project -force stt $out/project -part xck26-sfvc784-2LV-c
set_property board_part xilinx.com:kv260_som:part0:1.4 [current_project]
add_files [glob $root/rtl/frontend/*.sv $root/rtl/engine/*.sv $root/rtl/engine/*/*.sv]

create_bd_design system
create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:* ps
apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e -config {apply_board_preset "1"} [get_bd_cells ps]
set_property -dict {
 CONFIG.PSU__FPGA_PL0_ENABLE 1 CONFIG.PSU__FPGA_PL1_ENABLE 1
 CONFIG.PSU__USE__M_AXI_GP0 1 CONFIG.PSU__USE__M_AXI_GP1 0 CONFIG.PSU__USE__S_AXI_GP2 1
 CONFIG.PSU__SAXIGP2__DATA_WIDTH 128
 CONFIG.PSU__USE__IRQ0 0
} [get_bd_cells ps]
# KV260 Ubuntu boots with a 1.5 GHz IOPLL, so PL0 = 1500 / 15 = 100 MHz and PL1 = 1500 / 10 = 150 MHz
set_property -dict {
 CONFIG.PSU__PSS_REF_CLK__FREQMHZ 33.333333
 CONFIG.PSU__OVERRIDE__BASIC_CLOCK 1
 CONFIG.PSU__CRL_APB__IOPLL_CTRL__FBDIV 90
 CONFIG.PSU__CRL_APB__IOPLL_CTRL__DIV2 1
 CONFIG.PSU__CRL_APB__PL0_REF_CTRL__DIVISOR0 15
 CONFIG.PSU__CRL_APB__PL0_REF_CTRL__DIVISOR1 1
 CONFIG.PSU__CRL_APB__PL1_REF_CTRL__DIVISOR0 10
 CONFIG.PSU__CRL_APB__PL1_REF_CTRL__DIVISOR1 1
 CONFIG.PSU__CRL_APB__IOPLL_TO_FPD_CTRL__DIVISOR0 3
 CONFIG.PSU__CRL_APB__AMS_REF_CTRL__DIVISOR0 30
 CONFIG.PSU__CRL_APB__I2C1_REF_CTRL__DIVISOR0 15
 CONFIG.PSU__CRL_APB__SPI1_REF_CTRL__DIVISOR0 8
 CONFIG.PSU__CRL_APB__TIMESTAMP_REF_CTRL__DIVISOR0 15
 CONFIG.PSU__CRL_APB__LPD_SWITCH_CTRL__DIVISOR0 3
 CONFIG.PSU__CRL_APB__DBG_LPD_CTRL__DIVISOR0 6
 CONFIG.PSU__CRL_APB__LPD_LSBUS_CTRL__DIVISOR0 15
 CONFIG.PSU__CRL_APB__ADMA_REF_CTRL__DIVISOR0 3
 CONFIG.PSU__CRL_APB__IOU_SWITCH_CTRL__DIVISOR0 6
 CONFIG.PSU__CRL_APB__PCAP_CTRL__DIVISOR0 8
} [get_bd_cells ps]

# Clocks and resets with rst for 100 MHz, engine_rst for 150 MHz, fe_rst and eng_rst for the front end and the engine
# alone (GPIO, active high)
foreach {name clock} {rst pl_clk0 engine_rst pl_clk1 fe_rst pl_clk0 eng_rst pl_clk1} {
 create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:* $name
 connect_bd_net [get_bd_pins ps/$clock] [get_bd_pins $name/slowest_sync_clk]
 connect_bd_net [get_bd_pins ps/pl_resetn0] [get_bd_pins $name/ext_reset_in]
}
set_property CONFIG.C_AUX_RESET_HIGH 1 [get_bd_cells {fe_rst eng_rst}]
connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_pins ps/maxihpm0_fpd_aclk]
connect_bd_net [get_bd_pins ps/pl_clk1] [get_bd_pins ps/saxihp0_fpd_aclk]

# Register interconnect (PS -> peripherals) and memory interconnect (DMAs -> HP0), crossing both clocks
create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:* ctrl
set_property -dict {CONFIG.NUM_SI 1 CONFIG.NUM_MI 4 CONFIG.NUM_CLKS 2} [get_bd_cells ctrl]
connect_bd_intf_net [get_bd_intf_pins ps/M_AXI_HPM0_FPD] [get_bd_intf_pins ctrl/S00_AXI]
create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:* mem
set_property -dict {CONFIG.NUM_SI 4 CONFIG.NUM_MI 1 CONFIG.NUM_CLKS 2} [get_bd_cells mem]
connect_bd_intf_net [get_bd_intf_pins mem/M00_AXI] [get_bd_intf_pins ps/S_AXI_HP0_FPD]
foreach {cell primary secondary reset} {ctrl pl_clk0 pl_clk1 rst mem pl_clk1 pl_clk0 engine_rst} {
 connect_bd_net [get_bd_pins ps/$primary] [get_bd_pins $cell/aclk]
 connect_bd_net [get_bd_pins ps/$secondary] [get_bd_pins $cell/aclk1]
 connect_bd_net [get_bd_pins $reset/interconnect_aresetn] [get_bd_pins $cell/aresetn]
}

# A simple-mode AXI DMA between a stream module and DDR
proc add_dma {name ctrl_port read_port write_port in_bits out_bits clock reset} {
 create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:* $name
 set_property -dict [list CONFIG.c_include_sg 0 CONFIG.c_addr_width 64 CONFIG.c_sg_length_width 26 \
  CONFIG.c_m_axi_mm2s_data_width 128 CONFIG.c_m_axi_s2mm_data_width 128 CONFIG.c_include_mm2s_dre 1 \
  CONFIG.c_include_s2mm_dre 1 CONFIG.c_m_axis_mm2s_tdata_width $in_bits CONFIG.c_s_axis_s2mm_tdata_width $out_bits] \
  [get_bd_cells $name]
 connect_bd_intf_net [get_bd_intf_pins ctrl/$ctrl_port] [get_bd_intf_pins $name/S_AXI_LITE]
 connect_bd_intf_net [get_bd_intf_pins $name/M_AXI_MM2S] [get_bd_intf_pins mem/$read_port]
 connect_bd_intf_net [get_bd_intf_pins $name/M_AXI_S2MM] [get_bd_intf_pins mem/$write_port]
 foreach pin {s_axi_lite_aclk m_axi_mm2s_aclk m_axi_s2mm_aclk} {
  connect_bd_net [get_bd_pins ps/$clock] [get_bd_pins $name/$pin]
 }
 connect_bd_net [get_bd_pins $reset/peripheral_aresetn] [get_bd_pins $name/axi_resetn]
}

# Front end (milestone 3), its DMA, and the reset GPIO
create_bd_cell -type module -reference stt_frontend frontend
add_dma frontend_dma M01_AXI S00_AXI S01_AXI 16 32 pl_clk0 rst
connect_bd_intf_net [get_bd_intf_pins frontend_dma/M_AXIS_MM2S] [get_bd_intf_pins frontend/s_axis_samples]
connect_bd_intf_net [get_bd_intf_pins frontend/m_axis_features] [get_bd_intf_pins frontend_dma/S_AXIS_S2MM]
connect_bd_intf_net [get_bd_intf_pins ctrl/M00_AXI] [get_bd_intf_pins frontend/s_axi]
connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_pins frontend/aclk]
connect_bd_net [get_bd_pins fe_rst/peripheral_aresetn] [get_bd_pins frontend/aresetn]
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:* gpio
set_property -dict {
 CONFIG.C_GPIO_WIDTH 1 CONFIG.C_ALL_OUTPUTS 1 CONFIG.C_IS_DUAL 1 CONFIG.C_GPIO2_WIDTH 1 CONFIG.C_ALL_OUTPUTS_2 1
} [get_bd_cells gpio]
connect_bd_intf_net [get_bd_intf_pins ctrl/M02_AXI] [get_bd_intf_pins gpio/S_AXI]
connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_pins gpio/s_axi_aclk]
connect_bd_net [get_bd_pins rst/peripheral_aresetn] [get_bd_pins gpio/s_axi_aresetn]
connect_bd_net [get_bd_pins gpio/gpio_io_o] [get_bd_pins fe_rst/aux_reset_in]
connect_bd_net [get_bd_pins gpio/gpio2_io_o] [get_bd_pins eng_rst/aux_reset_in]
if {$mic} {
 foreach p {pmod_i2s_bclk pmod_i2s_lrclk pmod_i2s_dout pmod_led_d1 pmod_btn_sw1} {
  create_bd_port -dir [get_property DIR [get_bd_pins frontend/$p]] $p
  connect_bd_net [get_bd_ports $p] [get_bd_pins frontend/$p]
 }
} else {
 # Without the microphone its inputs are tied low and its outputs left unconnected
 create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:* zero
 set_property CONFIG.CONST_VAL 0 [get_bd_cells zero]
 foreach p {pmod_i2s_dout pmod_btn_sw1} {connect_bd_net [get_bd_pins zero/dout] [get_bd_pins frontend/$p]}
}

# Engine (milestone 4) and its DMA at 150 MHz
create_bd_cell -type module -reference stt_engine engine
add_dma engine_dma M03_AXI S02_AXI S03_AXI 128 128 pl_clk1 engine_rst
connect_bd_intf_net [get_bd_intf_pins engine_dma/M_AXIS_MM2S] [get_bd_intf_pins engine/s_axis]
connect_bd_intf_net [get_bd_intf_pins engine/m_axis] [get_bd_intf_pins engine_dma/S_AXIS_S2MM]
connect_bd_net [get_bd_pins ps/pl_clk1] [get_bd_pins engine/aclk]
connect_bd_net [get_bd_pins eng_rst/peripheral_aresetn] [get_bd_pins engine/aresetn]

foreach {base intf} {0xA0000000 frontend/s_axi 0xA0010000 frontend_dma/S_AXI_LITE 0xA0020000 gpio/S_AXI
                     0xA0030000 engine_dma/S_AXI_LITE} {
 assign_bd_address -offset $base -range 64K -target_address_space [get_bd_addr_spaces ps/Data] \
  [get_bd_addr_segs -of_objects [get_bd_intf_pins $intf]]
}
assign_bd_address
validate_bd_design
foreach {clock expected} {pl_clk0 100000000 pl_clk1 150000000} {
 set actual [get_property CONFIG.FREQ_HZ [get_bd_pins ps/$clock]]
 if {abs($actual - $expected) > 1000} {error "$clock realized $actual Hz, expected $expected"}
}
save_bd_design
make_wrapper -files [get_files system.bd] -top
add_files $out/project/stt.gen/sources_1/bd/system/hdl/system_wrapper.v
set_property top system_wrapper [current_fileset]

# PMOD pins. The slow I2S, LED and button need no I/O timing
if {$mic} {
 set x [open $out/ports.xdc w]
 set f [open $root/hw/kv260.xdc r]; puts -nonewline $x [read $f]; close $f
 puts $x {set_false_path -from [get_ports {pmod_i2s_dout pmod_btn_sw1}]}
 puts $x {set_false_path -to [get_ports {pmod_i2s_bclk pmod_i2s_lrclk pmod_led_d1}]}
 close $x
 add_files -fileset constrs_1 $out/ports.xdc
}
launch_runs synth_1 -jobs 8
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "Synthesis failed"}
launch_runs impl_1 -jobs 8
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {error "Implementation failed"}
open_run impl_1
# Up to three more optimisation passes while setup timing fails
for {set pass 0} {$pass < 3 && [get_property SLACK [get_timing_paths -max_paths 1]] < 0} {incr pass} {
 phys_opt_design -directive AggressiveExplore
}
report_timing_summary -file $out/timing.rpt
report_utilization -file $out/utilization.rpt
if {[get_property SLACK [get_timing_paths -max_paths 1]] < 0} {error "Setup timing failed"}
if {[get_property SLACK [get_timing_paths -delay_type min -max_paths 1]] < 0} {error "Hold timing failed"}
write_bitstream -force $out/stt-kv260.bit

# The overlay app with the bitstream as bootgen's .bit.bin, the device tree overlay and shell.json
set app $out/stt-kv260
file mkdir $app
cd $out
set f [open image.bif w]; puts $f "all: { stt-kv260.bit }"; close $f
set bootgen [file join $bin bootgen]
if {$tcl_platform(platform) eq "windows"} {set bootgen [list cmd /c $bootgen.bat]}
puts [exec {*}$bootgen -arch zynqmp -image image.bif -process_bitstream bin -w on 2>@1]
file rename -force stt-kv260.bit.bin $app/stt-kv260.bit.bin
puts [exec [file join $bin dtc] -@ -I dts -O dtb -o $app/stt-kv260.dtbo $root/hw/kv260.dtso 2>@1]
file copy -force $root/hw/shell.json $app
