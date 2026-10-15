set root [file normalize [file join [file dirname [info script]] ..]]
create_project ab_mac_conformance $root/build/ab_mac_conformance -part xck26-sfvc784-2LV-c -force
add_files $root/sim/qn_mac_ab_model.sv
add_files -fileset sim_1 $root/sim/tb_ab_mac_conformance.sv
set_property top tb_ab_mac_conformance [get_filesets sim_1]
set_property xsim.simulate.runtime 0ns [get_filesets sim_1]
launch_simulation
run all
close_sim
set f [open $root/build/ab_mac_conformance/ab_mac_conformance.sim/sim_1/behav/xsim/simulate.log r]
set result [read $f];close $f
if {![string match {*PASS: 203980 old/new vendor DSP and model checks*} $result]} {error "MAC input-register retiming failed"}
close_project
