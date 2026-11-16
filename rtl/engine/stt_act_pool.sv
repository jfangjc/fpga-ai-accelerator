/*
    activation banks -> 3 ultrarams of 20480 x 128 bits (320 kb each)
    -> one word is one channel x 16 frames and a channel's words sit next to each other -> address = channel * tiles + tile
    -> a 1024 channel x 320 frame output fills exactly one bank which is why they're this size
    -> one write port goes to the layer's dest bank and two read ports (a and b) read the source and branch banks
    -> a and b always ask for different banks since each bank only has one read port
*/
module stt_act_pool
    import stt_engine_pkg::*;
(
    input logic clk, we,
    input logic [1:0] write_bank,
    input logic [AW-1:0] write_address,
    input logic [127:0] write_data,
    input logic [1:0] a_bank, b_bank,
    input logic [AW-1:0] a_address, b_address,
    output logic [127:0] a_data, b_data          // LAT cycles after the address
);
    logic [127:0] data[BANKS];

    // each bank takes the write meant for it and reads whichever port is pointed at it
    for (genvar k = 0; k < BANKS; k++) begin : g_bank
        stt_sdp_ram #(.WIDTH(128), .DEPTH(BANK_WORDS), .LATENCY(LAT), .STYLE("ultra")) ram(
            clk, we && write_bank == k, write_address, write_data, a_bank == k ? a_address : b_address, data[k]);
    end

    assign a_data = data[a_bank];
    assign b_data = data[b_bank];
endmodule
