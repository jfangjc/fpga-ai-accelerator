# KV260 PMOD J2 pins 2 I2S WS, 4 I2S SD, 6 I2S SCK, 7 LED D1 and 8 button SW1, LVCMOS33 with slow slew and 4 mA
set_property PACKAGE_PIN B10 [get_ports pmod_i2s_lrclk] ;# PMOD pin 2 - som240_1_b20
set_property PACKAGE_PIN E12 [get_ports pmod_i2s_dout] ;# PMOD pin 4 - som240_1_b21
set_property PACKAGE_PIN D11 [get_ports pmod_i2s_bclk] ;# PMOD pin 6 - som240_1_b22
set_property PACKAGE_PIN C11 [get_ports pmod_led_d1] ;# PMOD pin 7 - som240_1_d22
set_property PACKAGE_PIN B11 [get_ports pmod_btn_sw1] ;# PMOD pin 8 - som240_1_c22
set_property IOSTANDARD LVCMOS33 [get_ports {pmod_i2s_lrclk pmod_i2s_dout pmod_i2s_bclk pmod_led_d1 pmod_btn_sw1}]
set_property SLEW SLOW [get_ports {pmod_i2s_lrclk pmod_i2s_bclk pmod_led_d1}]
set_property DRIVE 4 [get_ports {pmod_i2s_lrclk pmod_i2s_bclk pmod_led_d1}]
