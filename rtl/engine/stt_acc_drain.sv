// Accumulator drain: captures all OUTPUTS x FRAMES sums of a finished tile
// into a shadow register in one cycle (so the array continues with the next
// frame tile) and presents them one output row per cycle for OUTPUTS
// cycles. Each shadow cell is selected by its own replicated capture strobe
// and shifts unconditionally otherwise (row_sum is only meaningful while
// row_valid), so no 16k-bit enable net exists. The controller spaces tiles
// >= OUTPUTS cycles apart; a capture while more than one row is still pending
// is reported as a collision.
module stt_acc_drain #(
    parameter int OUTPUTS = 32,
    parameter int FRAMES = 16,
    parameter int SUM_W = 26,
    parameter int TAG_W = 1,
    localparam int IW = $clog2(OUTPUTS)
) (
    input  logic clk,
    input  logic rst,
    input  logic cap_valid,
    input  logic [OUTPUTS-1:0][FRAMES-1:0] cap_cell,
    input  logic [TAG_W-1:0] cap_tag,
    input  logic [OUTPUTS-1:0][FRAMES-1:0][SUM_W-1:0] cap_sum,
    output logic row_valid,
    output logic [IW-1:0] row_idx,
    output logic [TAG_W-1:0] row_tag,
    output logic [FRAMES-1:0][SUM_W-1:0] row_sum,
    output logic busy,
    output logic collision
);
    logic [OUTPUTS-1:0][FRAMES-1:0][SUM_W-1:0] shadow;
    logic [IW:0] left;   // rows still to present

    always_ff @(posedge clk) begin
        if (rst) begin
            left <= '0;
            collision <= 1'b0;
        end else if (cap_valid) begin
            if (left > 1) collision <= 1'b1;
            left <= (IW+1)'(OUTPUTS);
        end else if (left != 0) begin
            left <= left - 1'b1;
        end
        if (cap_valid) begin
            row_tag <= cap_tag;
            row_idx <= '0;
        end else if (left != 0) begin
            row_idx <= row_idx + 1'b1;
        end
    end

    for (genvar o = 0; o < OUTPUTS; o++) begin : g_row
        for (genvar t = 0; t < FRAMES; t++) begin : g_cell
            always_ff @(posedge clk)
                shadow[o][t] <= cap_cell[o][t] ? cap_sum[o][t]
                              : o == OUTPUTS-1 ? SUM_W'(0) : shadow[(o+1)%OUTPUTS][t];
        end
    end

    assign row_valid = left != 0;
    assign row_sum = shadow[0];
    assign busy = row_valid;
endmodule
