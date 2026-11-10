// Ping-pong weight tile buffer. One half holds the weights of one 32-channel
// output tile: kpt*16 rows (reduction terms) of 32 INT8 weights. Stream
// order is QNW1 [K row][output lane], i.e. two 16-byte beats per row (lanes
// 0..15, then 16..31). The writer fills halves alternately starting at half
// 0 and stalls while its target half is still full; the compute controller
// reads a full half for all frame tiles and then releases it. Thus the next
// tile's weights stream in while the current tile computes.
module stt_weight_buf #(
    parameter int LAT = 4
) (
    input  logic         clk,
    input  logic         rst,
    input  logic [6:0]   kpt,          // K tiles of 16 rows per output tile
    // Write side (command stream beats)
    input  logic [127:0] wr_data,
    input  logic         wr_valid,
    output logic         wr_ready,
    // Read side: one 32-lane row per cycle, LAT cycles latency
    input  logic         rd_en,
    input  logic         rd_half,
    input  logic [9:0]   rd_k,
    output logic [255:0] rd_data,
    output logic [1:0]   full,
    input  logic [1:0]   release_half
);
    logic       wh;        // half being written
    logic [9:0] wk;        // row being written
    logic       ws;        // 0: lanes 0..15, 1: lanes 16..31
    logic       fire, tile_end;
    logic [1:0] set_full;

    assign wr_ready = !full[wh];
    assign fire = wr_valid && wr_ready;
    assign tile_end = ws && wk == {6'(kpt - 7'd1), 4'hf};
    assign set_full = (fire && tile_end) ? (wh ? 2'b10 : 2'b01) : 2'b00;

    always_ff @(posedge clk) begin
        if (rst) begin
            wh <= 1'b0;
            wk <= '0;
            ws <= 1'b0;
            full <= '0;
        end else begin
            full <= (full | set_full) & ~release_half;
            if (fire) begin
                ws <= !ws;
                if (ws) begin
                    if (tile_end) begin
                        wk <= '0;
                        wh <= !wh;
                    end else begin
                        wk <= wk + 10'd1;
                    end
                end
            end
        end
    end

    stt_sdp_ram #(.WIDTH(128), .DEPTH(2048), .LATENCY(LAT), .STYLE("block")) ram_lo (
        .clk, .we(fire && !ws), .waddr({wh, wk}), .wdata(wr_data),
        .re(rd_en), .raddr({rd_half, rd_k}), .rdata(rd_data[127:0])
    );
    stt_sdp_ram #(.WIDTH(128), .DEPTH(2048), .LATENCY(LAT), .STYLE("block")) ram_hi (
        .clk, .we(fire && ws), .waddr({wh, wk}), .wdata(wr_data),
        .re(rd_en), .raddr({rd_half, rd_k}), .rdata(rd_data[255:128])
    );
endmodule
