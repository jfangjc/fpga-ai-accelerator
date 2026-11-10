# Resume an interrupted implementation without resetting synthesis/placement.
set root [file normalize [file join [file dirname [info script]] ..]]
set_param general.maxThreads 6
set out $root/build/hw/full150
open_project $out/project/stt.xpr
launch_runs impl_1 -to_step write_bitstream -jobs 6
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {error "Implementation failed"}
open_run impl_1
source $root/hw/repair_full150.tcl
