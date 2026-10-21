# DATA is sampled through a two-flop synchronizer in the 100 MHz domain.
# Exempt only the asynchronous pad -> first-stage D path. Subsequent stages
# and all receiver logic remain timed normally. No internal generated clocks.
set_false_path -from [get_ports {pmod_i2s_dout pmod_i2s2_dout}] -to [get_cells -hier -filter {NAME =~ */receiver/data_meta_reg}]
# Bound pad propagation of the enable-driven BCLK/WS waveform to one system
# clock period. Neither output is used as an internal clock. Receiver capture
# is deliberately late in BCLK high and must also be checked on the board.
set_max_delay -datapath_only 10.0 -from [get_cells -hier -filter {NAME =~ */receiver/mic_bclk_reg}] -to [get_ports {pmod_i2s_bclk pmod_i2s2_bclk}]
set_max_delay -datapath_only 10.0 -from [get_cells -hier -filter {NAME =~ */receiver/mic_ws_reg}] -to [get_ports {pmod_i2s_lrclk pmod_i2s2_lrclk}]
