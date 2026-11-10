# Keep generated IP timing exceptions; never declare the PS clocks asynchronous.
report_clocks -file $out/clocks.rpt
report_clock_interaction -delay_type min_max -file $out/clock-interaction.rpt
report_cdc -details -file $out/cdc.rpt
report_bus_skew -warn_on_violation -file $out/bus-skew.rpt
check_timing -verbose -file $out/check-timing.rpt
report_exceptions -file $out/exceptions.rpt
foreach src [get_clocks] {
 foreach dst [get_clocks] {
  set label "[get_property NAME $src]-to-[get_property NAME $dst]"
  regsub -all {[^a-zA-Z0-9_-]} $label _ label
  report_timing -from $src -to $dst -delay_type min_max -max_paths 10 -file $out/timing-$label.rpt
 }
}
