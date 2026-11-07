// Requantiser q = (p + 2^(n - 1) - (p < 0)) >>> n with p = sum * multiplier and n = 31 + shift
module stt_requant
    import stt_engine_pkg::*;
(
    input logic clk, rst,
    input logic [10:0] co,
    input logic [5:0] nt_out,
    input logic relu,
    input logic row_valid,
    input logic [4:0] row_index,
    input tile_tag_t row_tag,
    input logic [FRAMES - 1:0][31:0] row_sum,
    output logic [9:0] p_ch,
    input logic signed [31:0] p_mult, p_shift,
    output logic we,
    output logic [AW - 1:0] address,
    output logic [127:0] data
);
    localparam int STAGES = 9;
    logic [STAGES - 1:0] valid;
    logic [AW - 1:0] address_q[STAGES];
    logic signed [31:0] sum1[FRAMES], sum2[FRAMES], sum3[FRAMES], sum4[FRAMES], mult3, mult4;
    logic signed [63:0] product5[FRAMES], product6[FRAMES], rounded7[FRAMES], q8[FRAMES];
    logic [5:0] n[3:7];
    logic zero[3:7];
    logic [63:0] half[5:6], below[5:6];

    assign p_ch = {row_tag.otile, row_index};
    assign we = valid[STAGES - 1];
    assign address = address_q[STAGES - 1];

    always_ff @(posedge clk) begin
        valid <= rst ? 0 : {valid[STAGES - 2:0], row_valid && p_ch < co};
        address_q[0] <= row_tag.base + row_index * nt_out;
        for (int i = 1; i < STAGES; i++) address_q[i] <= address_q[i - 1];
        mult3 <= p_mult;
        mult4 <= mult3;
        n[3] <= 6'(31 + p_shift);
        zero[3] <= p_shift > 32;
        for (int i = 4; i <= 7; i++) begin
            n[i] <= n[i - 1];
            zero[i] <= zero[i - 1];
        end
        half[5] <= 64'd1 << (n[4] - 1);
        below[5] <= (64'd1 << (n[4] - 1)) - 1;
        half[6] <= half[5];
        below[6] <= below[5];
        for (int l = 0; l < FRAMES; l++) begin
            sum1[l] <= row_sum[l];
            sum2[l] <= sum1[l];
            sum3[l] <= sum2[l];
            sum4[l] <= sum3[l];
            product5[l] <= sum4[l] * mult4;
            product6[l] <= product5[l];
            rounded7[l] <= product6[l] + (product6[l] < 0 ? below[6] : half[6]);
            q8[l] <= zero[7] ? 0 : rounded7[l] >>> n[7];
            if (relu) begin
                data[8 * l+:8] <= q8[l] < 0 ? 0 : q8[l] > 255 ? 255 : 8'(q8[l]);
            end
            else begin
                data[8 * l+:8] <= q8[l] < -128 ? 8'h80 : q8[l] > 127 ? 127 : 8'(q8[l]);
            end
        end
    end
endmodule
