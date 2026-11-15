// Per-channel bias, multiplier and shift of the running layer
module stt_params
    import stt_engine_pkg::*;
(
    input logic clk, we,
    input logic [1:0] array,
    input logic [7:0] beat,
    input logic [127:0] wdata,
    input logic [9:0] ch,
    output logic [3:0][31:0] params,
    input logic [4:0] otile,
    output logic [ROWS-1:0][31:0] bias
);
    logic [127:0] word[4];
    logic [1:0] lane[PARAM_LAT];

    for (genvar i = 0; i < 4; i++) begin : g_array
        stt_sdp_ram #(.WIDTH(128), .DEPTH(256), .LATENCY(PARAM_LAT)) ram(
            clk, we && array == i, beat, wdata, ch[9:2], word[i]);
        assign params[i] = word[i][32*lane[PARAM_LAT-1]+:32];
    end
    always_ff @(posedge clk) begin
        lane[0] <= ch[1:0];
        for (int i = 1; i < PARAM_LAT; i++) lane[i] <= lane[i-1];
    end

    for (genvar o = 0; o < ROWS; o++) begin : g_bias
        (* ram_style = "distributed" *) logic [31:0] mem[32];
        always_ff @(posedge clk)
            if (we && array == 0 && beat[2:0] == o / 4) begin
                mem[beat[7:3]] <= wdata[32*(o%4)+:32];
            end
        assign bias[o] = mem[otile];
    end
endmodule
