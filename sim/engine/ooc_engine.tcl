# Independent synthesis and implementation at every target clock.
# Optional arguments are target frequencies in MHz (default: 100 150).
set threads 6
if {[info exists ::env(STT_OOC_THREADS)]} {set threads $::env(STT_OOC_THREADS)}
set_param general.maxThreads $threads
set root [file normalize [file join [file dirname [info script]] ../..]]
set output_root [file join $root build/sim/engine]
if {[info exists ::env(STT_OOC_ROOT)]} {set output_root [file normalize $::env(STT_OOC_ROOT)]}
set targets $argv
if {[llength $targets] == 0} {set targets {100 150}}
foreach mhz $targets {
    if {![string is double -strict $mhz] || $mhz <= 0} {error "Expected positive MHz, got $mhz"}
    set out [file join $output_root ooc_engine_$mhz]
    file mkdir $out
    set f [open [file join $out clock.xdc] w]
    puts $f "create_clock -name aclk -period [expr {1000.0/$mhz}] \[get_ports aclk\]"
    puts $f {set_property HD.CLK_SRC BUFGCE_X0Y0 [get_ports aclk]}
    close $f
        create_project -in_memory -part xck26-sfvc784-2LV-c
        read_verilog -sv [file join $root rtl/engine/stt_engine_pkg.sv]
        foreach src [glob [file join $root rtl/engine/*.sv]] {
            if {[file tail $src] ne "stt_engine_pkg.sv"} {read_verilog -sv $src}
        }
        read_xdc [file join $out clock.xdc]
        synth_design -top stt_engine -mode out_of_context
        report_utilization -file [file join $out synthesis_utilization.rpt]
        report_utilization -hierarchical -file [file join $out synthesis_hierarchy.rpt]
        write_checkpoint -force [file join $out synthesized.dcp]
    # Optional fast architecture check; never label this as routed evidence.
    if {[info exists ::env(STT_OOC_SYNTH_ONLY)] && $::env(STT_OOC_SYNTH_ONLY) eq "1"} {
        close_project
        continue
    }
    opt_design
    place_design
    route_design
    report_utilization -file [file join $out utilization.rpt]
    report_timing_summary -file [file join $out timing.rpt]
    report_timing -delay_type max -max_paths 50 -nworst 1 -file [file join $out setup_paths.rpt]
    report_timing -delay_type min -max_paths 20 -nworst 1 -file [file join $out hold_paths.rpt]
    # One worst path per failing endpoint gives additive TNS by path class.
    set f [open [file join $out failing_setup.csv] w]
    puts $f {slack,startpoint,endpoint}
    foreach p [get_timing_paths -setup -slack_lesser_than 0 -max_paths 100000 -nworst 1] {
        puts $f "[get_property SLACK $p],[get_property STARTPOINT_PIN $p],[get_property ENDPOINT_PIN $p]"
    }
    close $f
    write_checkpoint -force [file join $out routed.dcp]
    set worst [get_timing_paths -max_paths 1 -setup]
    set slack [get_property SLACK $worst]
    close_project
    puts "Target $mhz MHz WNS $slack"
}
