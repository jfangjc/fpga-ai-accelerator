# Run every RTL testbench in xsim with vivado -mode batch -source sim/run.tcl
set root [file normalize [file join [file dirname [info script]] ..]]
set bin [file join $::env(XILINX_VIVADO) bin]
set suites {
 frontend {tb_frontend_stream tb_frontend_control}
 engine   {tb_engine tb_engine_memory tb_engine_compute}
}

proc tool {log name args} {
 global bin tcl_platform
 set exe [file join $bin $name]
 if {$tcl_platform(platform) eq "windows"} {set cmd [list cmd /c "$exe.bat" {*}$args]} else {set cmd [list $exe {*}$args]}
 if {[catch {exec {*}$cmd >> $log 2>@1} message]} {
  puts "FAIL $name: see $log"
  return -code error $message
 }
}

proc run_suite {component tests} {
 global root
 set work [file join $root build sim $component]
 file mkdir $work
 cd $work
 # Packages compile first
 set sources [lsort [glob [file join $root rtl $component *.sv] [file join $root rtl $component * *.sv]]]
 set sources [concat [lsearch -all -inline $sources *_pkg.sv] [lsearch -all -inline -not $sources *_pkg.sv]]
 set passed 0
 foreach tb $tests {
  set log [file join $work $tb.log]
  file delete -force $log
  tool $log xvlog -sv -i [file join $root sim $component] {*}$sources [file join $root sim $component $tb.sv]
  tool $log xelab $tb -s $tb -timescale 1ns/1ps -O3
  tool $log xsim $tb -R
  set f [open $log]; set text [read $f]; close $f
  if {[regexp {(?m)^PASS} $text] && ![regexp {FAIL|Fatal|FATAL|ERROR:} $text]} {
   puts "PASS $tb"
   incr passed
  } else {
   puts "FAIL $tb"
   foreach line [split $text \n] {if {[regexp {FAIL|Fatal|FATAL|ERROR:} $line]} {puts "  $line"}}
  }
 }
 return [expr {$passed == [llength $tests]}]
}

set ok 1
foreach {component tests} $suites {
 if {![run_suite $component $tests]} {set ok 0}
}
if {!$ok} {error "RTL simulation failed"}
