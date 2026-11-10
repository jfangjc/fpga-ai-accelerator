set root [file normalize [file join [file dirname [info script]] ../..]]
cd [file join $root rtl/frontend]
file mkdir [file join $root build/sim/frontend]
read_verilog -sv [glob *.sv]
read_xdc [file join $root sim/frontend/ooc.xdc]
synth_design -top stt_frontend -part xck26-sfvc784-2LV-c -mode out_of_context
set_property HD.CLK_SRC BUFGCE_X0Y0 [get_ports aclk]
opt_design
place_design
route_design
report_utilization -file [file join $root build/sim/frontend/utilization.rpt]
report_timing_summary -file [file join $root build/sim/frontend/timing.rpt]
write_checkpoint -force [file join $root build/sim/frontend/frontend_routed.dcp]
