`timescale 1ns/1ps
// Nominal 100 MHz PS clock (99,999,001 Hz board preset), 1.024 MHz BCLK.
// NCO compensates the preset frequency; 64 BCLK/frame = 16 kHz.
// channel_right selects the slot matching the microphone's SELECT wiring.
// The first bit of each slot is the I2S delay bit; the following 24 bits form
// one sample. A grounded SELECT transmits left, a high SELECT transmits right.
module sph0645_stream_rx (
    input wire clk,
    input wire rst,
    input wire mic_data,
    input wire channel_right,
    output reg mic_bclk,
    output reg mic_ws,
    output reg [23:0] sample_data,
    output reg sample_valid
);
    localparam [31:0] EDGE_INCREMENT = 32'd87961809;
    reg [31:0] phase;
    wire [32:0] next_phase = {1'b0,phase} + {1'b0,EDGE_INCREMENT};
    (* ASYNC_REG = "TRUE" *) reg data_meta, data_sync;
    reg [5:0] bit_index;
    reg [23:0] shift;
    always @(posedge clk) begin
        if (rst) begin
            phase <= 0;
            mic_bclk <= 0;
            mic_ws <= 1;
            bit_index <= 63;
            shift <= 0;
            sample_data <= 0;
            sample_valid <= 0;
            data_meta <= 0;
            data_sync <= 0;
        end else begin
            data_meta <= mic_data;
            data_sync <= data_meta;
            phase <= next_phase[31:0];
            sample_valid <= 0;
            if (next_phase[32]) begin
                mic_bclk <= ~mic_bclk;
                if (mic_bclk) begin
                    // Falling edge is ~488 ns after the microphone's rising
                    // edge launch, comfortably past its 66 ns data delay.
                    if ((!channel_right && bit_index >= 1 && bit_index <= 24) ||
                        (channel_right && bit_index >= 33 && bit_index <= 56)) begin
                        shift <= {shift[22:0],data_sync};
                        if (bit_index == (channel_right ? 56 : 24)) begin
                            sample_data <= {shift[22:0],data_sync};
                            sample_valid <= 1;
                        end
                    end
                    bit_index <= bit_index + 1'b1;
                    if (bit_index == 63) mic_ws <= 0;
                    if (bit_index == 31) mic_ws <= 1;
                end
            end
        end
    end
endmodule
