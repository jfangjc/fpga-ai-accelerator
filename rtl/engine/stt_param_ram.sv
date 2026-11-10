// Per-output-channel bias, Q31 multiplier and shift of the current layer.
// Each INT32 array arrives as 16-byte beats of four channels, so each RAM
// stores four channels per word (256 words cover 1024 channels). A read
// returns the three values of channel ch PARAM_LAT cycles after re.
module stt_param_ram
    import stt_engine_pkg::*;
(
    input  logic         clk,
    input  logic         we,
    input  logic [1:0]   sel,     // 0 bias, 1 multiplier, 2 shift
    input  logic [7:0]   waddr,   // beat index within the array
    input  logic [127:0] wdata,
    input  logic         re,
    input  logic [9:0]   ch,
    output logic [31:0]  bias,
    output logic [31:0]  mult,
    output logic [31:0]  shift
);
    logic [127:0] word [3];
    logic [1:0]   lane [PARAM_LAT];

    for (genvar i = 0; i < 3; i++) begin : g_ram
        stt_sdp_ram #(.WIDTH(128), .DEPTH(256), .LATENCY(PARAM_LAT), .STYLE("block")) ram (
            .clk, .we(we && sel == 2'(i)), .waddr, .wdata,
            .re, .raddr(ch[9:2]), .rdata(word[i])
        );
    end

    always_ff @(posedge clk) begin
        lane[0] <= ch[1:0];
        for (int i = 1; i < PARAM_LAT; i++) lane[i] <= lane[i-1];
    end

    assign bias  = word[0][32*lane[PARAM_LAT-1] +: 32];
    assign mult  = word[1][32*lane[PARAM_LAT-1] +: 32];
    assign shift = word[2][32*lane[PARAM_LAT-1] +: 32];
endmodule
