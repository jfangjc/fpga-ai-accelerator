// Q31 requantizer (docs/quant_spec.md): one rounding after the full signed
// 64-bit product, ties away from zero, then clamp to [lo, hi]:
//   n = 31 + shift; q = sign(p) * ((|p| + 2^(n-1)) >> n); q = 0 if n >= 64.
// Eight elastic stages (stt_engine_pkg::REQUANT_LAT) share one advance
// enable, so accepted data stays ordered under backpressure; with m_ready
// tied high the latency is exactly eight cycles. The multiply has two
// register stages so it maps onto pipelined DSP48E2 cascades.
// m_error flags multiplier <= 0, shift < -30 (n < 1) or lo > hi.
module stt_requant (
    input  logic clk,
    input  logic rst,
    input  logic s_valid,
    output logic s_ready,
    input  logic signed [31:0] s_acc, s_mult, s_shift, s_lo, s_hi,
    output logic m_valid,
    input  logic m_ready,
    output logic signed [31:0] m_value,
    output logic m_error
);
    localparam int STAGES = 8;
    logic [STAGES-1:0] valid;
    logic advance;
    assign advance = !valid[STAGES-1] || m_ready;
    assign s_ready = advance;
    assign m_valid = valid[STAGES-1];

    // Sideband carried beside the arithmetic, indexed by stage 1..7.
    logic        zero [1:7];   // n >= 64: result is zero
    logic        err  [1:7];
    logic [5:0]  n    [1:5];   // shift amount 1..63 when !zero && !err
    logic signed [31:0] lo [1:7];
    logic signed [31:0] hi [1:7];
    logic        neg  [4:6];

    logic signed [32:0] n_full;
    logic signed [31:0] acc1, mult1;
    (* use_dsp = "yes" *) logic signed [63:0] prod2;
    logic signed [63:0] prod3;
    logic [61:0] mag4;          // |p| <= 2^31 * (2^31-1) < 2^62
    logic [62:0] sum5;          // |p| + 2^(n-1) < 2^63
    logic [62:0] q6;
    logic signed [63:0] v7;

    assign n_full = 33'sd31 + {s_shift[31], s_shift};

    always_ff @(posedge clk) begin
        if (rst) valid <= '0;
        else if (advance) valid <= {valid[STAGES-2:0], s_valid};
    end

    always_ff @(posedge clk) begin
        if (advance) begin
            // 1: operands and decoded shift
            acc1 <= s_acc;
            mult1 <= s_mult;
            zero[1] <= n_full >= 33'sd64;
            n[1] <= n_full[5:0];
            err[1] <= s_mult <= 0 || s_shift < -32'sd30 || s_lo > s_hi;
            lo[1] <= s_lo;
            hi[1] <= s_hi;
            // 2-3: 32x32 signed product
            prod2 <= acc1 * mult1;
            prod3 <= prod2;
            // 4: magnitude and sign
            neg[4] <= prod3[63];
            mag4 <= prod3[63] ? 62'(-prod3) : prod3[61:0];
            // 5: add the rounding constant 2^(n-1)
            sum5 <= {1'b0, mag4} + (63'd1 << (n[4] - 6'd1));
            // 6: shift (n >= 64 rounds every legal product to zero)
            q6 <= zero[5] ? 63'd0 : sum5 >> n[5];
            // 7: restore the sign
            v7 <= neg[6] ? -$signed({1'b0, q6}) : $signed({1'b0, q6});
            // 8: clamp
            if (v7 < 64'(lo[7])) m_value <= lo[7];
            else if (v7 > 64'(hi[7])) m_value <= hi[7];
            else m_value <= v7[31:0];
            m_error <= err[7];
            for (int i = 2; i <= 7; i++) begin
                zero[i] <= zero[i-1];
                err[i] <= err[i-1];
                lo[i] <= lo[i-1];
                hi[i] <= hi[i-1];
            end
            for (int i = 2; i <= 5; i++) n[i] <= n[i-1];
            neg[5] <= neg[4];
            neg[6] <= neg[5];
        end
    end
endmodule
