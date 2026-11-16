/*
    ping pong weight buffer -> two halves that each hold one 32 output channel tile
    -> the dma fills one half while a unit reads the other so the next tile's weights are ready when it needs them
    -> a row is 256 bits (32 int8 weights) so it takes two 128 bit beats -> low half then high half
    -> a half is full once kpt * 16 rows are in and it stays full until the unit hands it back
*/
module stt_weight_buf
    import stt_engine_pkg::*;
(
    input logic clk, rst,
    input logic [6:0] kpt,                   // rows per tile / 16
    input logic [127:0] wr_data,             // weight beats from the dma
    input logic wr_valid,
    output logic wr_ready,                   // low while the half being written is still full -> stalls the dma
    input logic rd_half,
    input logic [9:0] rd_k,                  // row to read -> input channel for pointwise or tap for depthwise
    output logic [255:0] rd_data,            // that row's 32 weights
    output logic [1:0] full,                 // which halves hold a whole tile
    input logic [1:0] release_half           // one cycle pulse from the unit once it's done with a half
);
    logic half, lanes, write, tile_end;
    logic [9:0] row;

    assign wr_ready = !full[half];
    assign write = wr_valid && wr_ready;
    assign tile_end = lanes && row == {kpt, 4'h0} - 1;

    // counts beats into rows and rows into a tile then flips to the other half
    always_ff @(posedge clk) begin
        if (rst) begin
            half <= 0;
            row <= 0;
            lanes <= 0;
            full <= 0;
        end
        else begin
            full <= (full | (write && tile_end ? 2'b01 << half : 2'b00)) & ~release_half;
            if (write) begin
                lanes <= !lanes;
                if (tile_end) begin
                    row <= 0;
                    half <= !half;
                end
                else if (lanes) begin
                    row <= row + 1;
                end
            end
        end
    end

    // two 128 bit rams side by side make one 256 bit row -> the half bit is the top address bit
    stt_sdp_ram #(.WIDTH(128), .DEPTH(2048), .LATENCY(LAT)) low(
        clk, write && !lanes, {half, row}, wr_data, {rd_half, rd_k}, rd_data[127:0]);
    stt_sdp_ram #(.WIDTH(128), .DEPTH(2048), .LATENCY(LAT)) high(
        clk, write && lanes, {half, row}, wr_data, {rd_half, rd_k}, rd_data[255:128]);
endmodule
