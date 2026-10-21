`timescale 1ns/1ps
// Both custom-carrier microphone connectors receive the same I2S clocks.
// The capture configuration selects either DATA input and either I2S slot.
module moonshine_stream_top (
 output pmod_i2s_lrclk, output pmod_i2s_bclk, input pmod_i2s_dout,
 output pmod_i2s2_lrclk, output pmod_i2s2_bclk, input pmod_i2s2_dout,
 output pmod_led_d1, input pmod_btn_sw1
);
 asr_v2_system_wrapper system_i(.mic_bclk(pmod_i2s_bclk),
   .mic_ws(pmod_i2s_lrclk), .mic_data(pmod_i2s_dout), .mic_data_alt(pmod_i2s2_dout));
 assign pmod_i2s2_lrclk = pmod_i2s_lrclk;
 assign pmod_i2s2_bclk = pmod_i2s_bclk;
 assign pmod_led_d1 = 1'b1;
endmodule
