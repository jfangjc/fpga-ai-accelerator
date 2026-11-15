// Ping-pong weight buffer of two 32-channel output tiles
module stt_weight_buf
    import stt_engine_pkg::*;
(
    input logic clk, rst,
    input logic [6:0] kpt,
    input logic [127:0] wr_data,
    input logic wr_valid,
    output logic wr_ready,
    input logic rd_half,
    input logic [9:0] rd_k,
    output logic [255:0] rd_data,
    output logic [1:0] full,
    input logic [1:0] release_half
);
    logic half, lanes, write, tile_end;
    logic [9:0] row;

    assign wr_ready = !full[half];
    assign write = wr_valid && wr_ready;
    assign tile_end = lanes && row == {kpt, 4'h0} - 1;

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

    stt_sdp_ram #(.WIDTH(128), .DEPTH(2048), .LATENCY(LAT)) low(
        clk, write && !lanes, {half, row}, wr_data, {rd_half, rd_k}, rd_data[127:0]);
    stt_sdp_ram #(.WIDTH(128), .DEPTH(2048), .LATENCY(LAT)) high(
        clk, write && lanes, {half, row}, wr_data, {rd_half, rd_k}, rd_data[255:128]);
endmodule
