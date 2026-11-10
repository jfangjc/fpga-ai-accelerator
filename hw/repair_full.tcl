set root [file normalize [file join [file dirname [info script]] ..]]
set_param general.maxThreads 6
open_project -read_only $root/build/hw/full/project/stt.xpr
open_run impl_1
source $root/hw/full_timing.tcl
report_timing_summary -file $root/build/hw/full/timing.rpt
report_utilization -file $root/build/hw/full/utilization.rpt
set setup [get_property SLACK [lindex [get_timing_paths -max_paths 1] 0]]
set hold [get_property SLACK [lindex [get_timing_paths -delay_type min -max_paths 1] 0]]
if {$setup < 0 || $hold < 0} {error "100 MHz closure failed: setup $setup hold $hold"}
write_bitstream -force $root/build/hw/full/stt-full.bit
write_hw_platform -fixed -include_bit -force -file $root/build/hw/full/stt-full.xsa
