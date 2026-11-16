/*
    per channel numbers for the layer that's running
    -> four arrays come in during PARAMS -> bias then mult then shift for a convolution or mult a then shift a then mult b then shift b for an add
    -> each 128 bit beat holds 4 channels' int32 values
    -> the requantiser and the add look up one channel at a time through params
    -> the mac array needs all 32 rows' biases at once so the bias also goes into 32 small rams (one per row)
*/
module stt_params
    import stt_engine_pkg::*;
(
    input logic clk, we,
    input logic [1:0] array,                 // which of the four arrays this beat belongs to
    input logic [7:0] beat,                  // beat number in that array -> channels 4 * beat to 4 * beat + 3
    input logic [127:0] wdata,
    input logic [9:0] ch,                    // channel to look up
    output logic [3:0][31:0] params,         // that channel's value from every array -> PARAM_LAT cycles later
    input logic [4:0] otile,                 // output tile whose biases the array needs
    output logic [ROWS-1:0][31:0] bias       // biases for channels otile * 32 to otile * 32 + 31
);
    logic [127:0] word[4];
    logic [1:0] lane[PARAM_LAT];

    // NOTE: lookup by channel

    for (genvar i = 0; i < 4; i++) begin : g_array
        stt_sdp_ram #(.WIDTH(128), .DEPTH(256), .LATENCY(PARAM_LAT)) ram(
            clk, we && array == i, beat, wdata, ch[9:2], word[i]);
        assign params[i] = word[i][32*lane[PARAM_LAT-1]+:32];
    end
    // keeps which of the 4 values in the word was asked for until the read comes back
    always_ff @(posedge clk) begin
        lane[0] <= ch[1:0];
        for (int i = 1; i < PARAM_LAT; i++) lane[i] <= lane[i-1];
    end

    // NOTE: biases for the whole array

    for (genvar o = 0; o < ROWS; o++) begin : g_bias
        (* ram_style = "distributed" *) logic [31:0] mem[32];
        // row o keeps the bias of channel o in every output tile -> beat[7:3] is the tile
        always_ff @(posedge clk)
            if (we && array == 0 && beat[2:0] == o / 4) begin
                mem[beat[7:3]] <= wdata[32*(o%4)+:32];
            end
        assign bias[o] = mem[otile];
    end
endmodule
