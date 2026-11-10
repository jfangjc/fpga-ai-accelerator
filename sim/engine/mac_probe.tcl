set_param general.maxThreads 8
create_project -in_memory -part xck26-sfvc784-2LV-c
read_verilog -sv rtl/engine/stt_mac_array.sv
read_xdc sim/engine/mac_probe.xdc
synth_design -top stt_mac_array -mode out_of_context -generic OUTPUTS=1 -generic FRAMES=1
report_utilization -file sim/engine/mac_probe.rpt

