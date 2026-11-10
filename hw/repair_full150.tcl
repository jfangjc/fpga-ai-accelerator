set root [file normalize [file join [file dirname [info script]] ..]]
set out $root/build/hw/full150
set_param general.maxThreads 6
if {[current_project -quiet] eq ""} {
 open_project -read_only $out/project/stt.xpr
 open_run impl_1
}
source $root/hw/full150_timing.tcl
report_timing_summary -report_unconstrained -file $out/timing.rpt
report_utilization -file $out/utilization.rpt
source $root/hw/report_full150.tcl
set setup [get_property SLACK [lindex [get_timing_paths -max_paths 1] 0]]
set hold [get_property SLACK [lindex [get_timing_paths -delay_type min -max_paths 1] 0]]
if {$setup < 0 || $hold < 0} {error "100/150 MHz closure failed: setup $setup hold $hold"}
write_checkpoint -force $out/accepted.dcp
write_bitstream -force $out/stt-full150.bit
write_hw_platform -fixed -include_bit -force -file $out/stt-full150.xsa
