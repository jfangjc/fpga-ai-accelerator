# Implementation-only closure for the frozen stt-full RTL. Also usable as a
# ROUTE_DESIGN.TCL.POST hook; never relax the 100 MHz clock or timing checks.
set full_root [file normalize [file join [file dirname [info script]] ..]]
set full_out $full_root/build/hw/full
for {set pass 1} {$pass <= 3} {incr pass} {
 set before [get_property SLACK [lindex [get_timing_paths -max_paths 1] 0]]
 if {$before >= 0} {break}
 phys_opt_design -directive AggressiveExplore
 report_timing_summary -file $full_out/timing-closure-$pass.rpt
 write_checkpoint -force $full_out/closure-$pass.dcp
 set after [get_property SLACK [lindex [get_timing_paths -max_paths 1] 0]]
 puts "stt-full closure pass $pass: WNS $before -> $after"
 if {$after <= $before} {break}
}
