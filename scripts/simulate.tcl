# Run one selected regression in a disposable project under build/.
set root [file normalize [file join [file dirname [info script]] ..]]
if {[llength $argv]!=1} {error "Expected a regression name"}
set name [lindex $argv 0]
if {$name ni {ab_mac_conformance branch512_ab_fast_sim branch512_ab_reset_sim branch512_ab_payload_reset_sim}} {error "Unknown regression"}
file mkdir $root/build $root/reports/local
source $root/scripts/$name.tcl
