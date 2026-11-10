# Post-route optimisation without clock or timing-exception changes.
set full150_root [file normalize [file join [file dirname [info script]] ..]]
set full150_out $full150_root/build/hw/full150
for {set pass 1} {$pass <= 3} {incr pass} {
 set before [get_property SLACK [lindex [get_timing_paths -max_paths 1] 0]]
 if {$before >= 0} {break}
 phys_opt_design -directive AggressiveExplore
 report_timing_summary -file $full150_out/timing-closure-$pass.rpt
 write_checkpoint -force $full150_out/closure-$pass.dcp
 set after [get_property SLACK [lindex [get_timing_paths -max_paths 1] 0]]
 puts "stt-full150 closure pass $pass: WNS $before -> $after"
 if {$after <= $before} {break}
}
