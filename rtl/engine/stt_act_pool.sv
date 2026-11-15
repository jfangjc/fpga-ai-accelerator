// Activation banks: one write port, read ports A and B
module stt_act_pool
    import stt_engine_pkg::*;
(
    input logic clk, we,
    input logic [1:0] write_bank,
    input logic [AW-1:0] write_address,
    input logic [127:0] write_data,
    input logic [1:0] a_bank, b_bank,
    input logic [AW-1:0] a_address, b_address,
    output logic [127:0] a_data, b_data
);
    logic [127:0] data[BANKS];

    for (genvar k = 0; k < BANKS; k++) begin : g_bank
        stt_sdp_ram #(.WIDTH(128), .DEPTH(BANK_WORDS), .LATENCY(LAT), .STYLE("ultra")) ram(
            clk, we && write_bank == k, write_address, write_data, a_bank == k ? a_address : b_address, data[k]);
    end

    assign a_data = data[a_bank];
    assign b_data = data[b_bank];
endmodule
