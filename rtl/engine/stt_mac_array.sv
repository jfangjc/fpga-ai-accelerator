// Output-stationary INT8 MAC array: OUTPUTS channels x FRAMES frames.
// Every accepted term broadcasts one signed INT8 weight per output row and
// one signed 9-bit activation per frame column (INT8 sign-extended or UINT8
// zero-extended), so each weight is reused FRAMES times and each activation
// OUTPUTS times without operand skew or wavefront fill.
//
// Each cell maps to exactly one DSP48E2: A/B input registers, M register,
// and an accumulating P register. The term flagged first restarts the
// accumulation (P = bias + M instead of P + M), so frame tiles run back to back.
//
// Timing: term at cycle c -> A/B (c+1) -> M (c+2) -> P (c+3). out_valid is
// high for exactly the one cycle in which P holds the complete sums of a
// tile whose final term carried in_last; the consumer must capture then,
// because the next tile's first term overwrites P at the end of that cycle.
// Sums include bias and use 33 bits; every prefix is checked for INT32 overflow.
// out_cap repeats out_valid once per cell: the drain samples all 16k sum bits
// in that one cycle. max_fanout lets synthesis replicate the strobe register
// (a kept per-cell register also met 150 MHz but blocked LUT combining, +7.7k LUT).
(* use_dsp = "yes" *)
module stt_mac_array #(
    parameter int OUTPUTS = 32,
    parameter int FRAMES = 16,
    parameter int SUM_W = 33,
    parameter int TAG_W = 1
) (
    input  logic clk,
    input  logic rst,
    input  logic in_valid,
    input  logic in_first,
    input  logic in_last,
    input  logic [TAG_W-1:0] in_tag,
    input  logic [OUTPUTS-1:0][7:0] weight,
    input  logic [FRAMES-1:0][8:0] activation,
    input  logic [OUTPUTS-1:0][31:0] bias,
    output logic overflow,
    output logic out_valid,
    output logic [OUTPUTS-1:0][FRAMES-1:0] out_cap,
    output logic [TAG_W-1:0] out_tag,
    output logic [OUTPUTS-1:0][FRAMES-1:0][SUM_W-1:0] sum,
    output logic busy
);
    // Control pipeline aligned with the A/B (1) and M (2) registers. The
    // stage-2 enables drive every DSP's CEP/OPMODE: let synthesis replicate.
    logic v1, f1, l1;
    (* max_fanout = 32 *) logic v2;
    (* max_fanout = 32 *) logic f2;
    (* max_fanout = 32 *) logic l2;
    logic [TAG_W-1:0] tag1, tag2;
    logic [OUTPUTS-1:0][FRAMES-1:0] overflow_cell;
    (* max_fanout = 64 *) logic cap;
    assign out_cap = {(OUTPUTS*FRAMES){cap}};
    assign overflow = |overflow_cell;

    always_ff @(posedge clk) begin
        if (rst) begin
            v1 <= 1'b0;
            v2 <= 1'b0;
            out_valid <= 1'b0;
            cap <= 1'b0;
        end else begin
            v1 <= in_valid;
            v2 <= v1;
            out_valid <= v2 && l2;
            cap <= v2 && l2;
        end
        f1 <= in_first;
        f2 <= f1;
        l1 <= in_last;
        l2 <= l1;
        tag1 <= in_tag;
        tag2 <= tag1;
        out_tag <= tag2;
    end
    assign busy = v1 || v2 || out_valid;

    for (genvar o = 0; o < OUTPUTS; o++) begin : g_row
        for (genvar t = 0; t < FRAMES; t++) begin : g_cell
            logic signed [8:0]       a1;
            logic signed [7:0]       b1;
            logic signed [SUM_W-1:0] m2;
            logic signed [SUM_W-1:0] p3;
            logic signed [SUM_W-1:0] feedback;
            logic signed [31:0] bias1, bias2;
            logic p_valid;
            assign feedback = f2 ? SUM_W'(bias2) : p3;
            always_ff @(posedge clk) begin
                // Operand pipeline enables avoid toggling the idle pointwise
                // array while the depthwise controller owns the shared RAMs.
                if (in_valid) begin
                    bias1 <= bias[o];
                    a1 <= activation[t];
                    b1 <= weight[o];
                end
                if (v1) begin
                    bias2 <= bias1;
                    m2 <= a1 * b1;
                end
                if (v2) p3 <= feedback + m2;
                if (rst) begin
                    p_valid <= 1'b0;
                    overflow_cell[o][t] <= 1'b0;
                end else begin
                    if (v2) p_valid <= 1'b1;
                    if (p_valid && p3[SUM_W-1:31] != {(SUM_W-31){p3[31]}})
                        overflow_cell[o][t] <= 1'b1;
                end
            end
            assign sum[o][t] = p3;
        end
    end
endmodule


