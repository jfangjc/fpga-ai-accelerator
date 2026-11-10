# Resume an interrupted stt-full implementation from impl_1's post-place
# phys_opt checkpoint: the same route_design step, the full_timing.tcl
# closure hook, the 100 MHz setup/hold gates and write_bitstream.
# vivado.bat -mode batch -source hw/route_full.tcl
set root [file normalize [file join [file dirname [info script]] ..]]
set out $root/build/hw/full
set impl $out/project/stt.runs/impl_1
set_param general.maxThreads 6
open_checkpoint $impl/system_wrapper_physopt.dcp
route_design
write_checkpoint -force $out/routed.dcp
report_timing_summary -file $out/timing-routed.rpt
source $root/hw/full_timing.tcl
report_route_status -file $out/route_status.rpt
report_timing_summary -file $out/timing.rpt
report_utilization -file $out/utilization.rpt
report_power -file $out/power.rpt
set setup [get_property SLACK [lindex [get_timing_paths -max_paths 1] 0]]
set hold [get_property SLACK [lindex [get_timing_paths -delay_type min -max_paths 1] 0]]
puts "stt-full final: setup WNS $setup hold WHS $hold"
if {$setup < 0 || $hold < 0} {error "100 MHz closure failed: setup $setup hold $hold"}
write_checkpoint -force $out/final.dcp
write_bitstream -force $out/stt-full.bit
