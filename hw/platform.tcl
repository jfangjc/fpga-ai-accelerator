# DMA helper is shared by loopback and module-reference pipelines.
# sg_port: empty for simple mode (loopback); otherwise the mem SmartConnect slave
# port for the scatter-gather descriptor master (cyclic/chained BD rings).
proc add_dma {name ctrl_port mem_read mem_write irq0 irq1 base {axis_bits 128} {sg_port ""} {clock ps/pl_clk0} {reset rst/peripheral_aresetn}} {
 create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:* $name
 set sg [expr {$sg_port ne ""}]
 set_property -dict [list CONFIG.c_include_sg $sg CONFIG.c_addr_width 64 CONFIG.c_m_axi_mm2s_data_width 128 CONFIG.c_m_axi_s2mm_data_width 128 CONFIG.c_m_axis_mm2s_tdata_width $axis_bits CONFIG.c_s_axis_s2mm_tdata_width $axis_bits CONFIG.c_include_mm2s_dre 1 CONFIG.c_include_s2mm_dre 1 CONFIG.c_sg_length_width 26] [get_bd_cells $name]
 # SG mode defaults to the optional control/status app streams. Nothing drives
 # s_axis_s2mm_sts (tied low), so S2MM descriptors would never complete.
 if {$sg} {set_property CONFIG.c_sg_include_stscntrl_strm 0 [get_bd_cells $name]}
 connect_bd_intf_net [get_bd_intf_pins ctrl/$ctrl_port] [get_bd_intf_pins $name/S_AXI_LITE]
 connect_bd_intf_net [get_bd_intf_pins $name/M_AXI_MM2S] [get_bd_intf_pins mem/$mem_read]
 connect_bd_intf_net [get_bd_intf_pins $name/M_AXI_S2MM] [get_bd_intf_pins mem/$mem_write]
 set clocks {s_axi_lite_aclk m_axi_mm2s_aclk m_axi_s2mm_aclk}
 if {$sg} {
  connect_bd_intf_net [get_bd_intf_pins $name/M_AXI_SG] [get_bd_intf_pins mem/$sg_port]
  lappend clocks m_axi_sg_aclk
 }
 connect_bd_net [get_bd_pins $name/mm2s_introut] [get_bd_pins irqs/$irq0]
 connect_bd_net [get_bd_pins $name/s2mm_introut] [get_bd_pins [expr {[string first / $irq1] >= 0 ? $irq1 : "irqs/$irq1"}]]
 foreach p $clocks {connect_bd_net [get_bd_pins $clock] [get_bd_pins $name/$p]}
 connect_bd_net [get_bd_pins $reset] [get_bd_pins $name/axi_resetn]
 assign_bd_address -offset $base -range 64K -target_address_space [get_bd_addr_spaces ps/Data] [get_bd_addr_segs $name/S_AXI_LITE/Reg]
}
# Expand ctrl.NUM_MI, mem.NUM_SI, irqs.NUM_PORTS before using this helper.
# Module ports: s_axi, s_axis, m_axis, irq, aclk, aresetn (Vivado inferred interfaces).
proc add_rtl_pipeline {name module ctrl_ip ctrl_dma mem_read mem_write irq_ip irq_tx irq_rx base_ip base_dma {axis_bits 128} {stream_in s_axis} {stream_out m_axis} {sg_port ""}} {
 create_bd_cell -type module -reference $module $name
 add_dma ${name}_dma $ctrl_dma $mem_read $mem_write $irq_tx $irq_rx $base_dma $axis_bits $sg_port
 connect_bd_intf_net [get_bd_intf_pins ctrl/$ctrl_ip] [get_bd_intf_pins $name/s_axi]
 connect_bd_intf_net [get_bd_intf_pins ${name}_dma/M_AXIS_MM2S] [get_bd_intf_pins $name/$stream_in]
 connect_bd_intf_net [get_bd_intf_pins $name/$stream_out] [get_bd_intf_pins ${name}_dma/S_AXIS_S2MM]
 connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_pins $name/aclk]
 connect_bd_net [get_bd_pins rst/peripheral_aresetn] [get_bd_pins $name/aresetn]
 connect_bd_net [get_bd_pins $name/irq] [get_bd_pins irqs/$irq_ip]
 assign_bd_address -offset $base_ip -range 64K -target_address_space [get_bd_addr_spaces ps/Data] [get_bd_addr_segs -of_objects [get_bd_intf_pins $name/s_axi]]
}
