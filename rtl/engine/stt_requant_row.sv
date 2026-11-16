/*
    requantiser -> turns one output channel's 16 int32 sums into 16 bytes for the dest bank
    -> q = (p + 2^(n-1) - (p < 0)) >>> n with p = sum * multiplier and n = 31 + shift
    -> mult and shift together scale by input_scale * weight_scale / output_scale which convert.py worked out offline
    -> halves round away from zero then relu layers clamp to uint8 and the rest clamp to int8
    -> the 16 bytes make one 128 bit word which is one channel x 16 frames like every other bank word
*/
module stt_requant_row
    import stt_engine_pkg::*;
(
    input logic clk, rst,
    input logic [10:0] co,                       // output channels -> padded rows past this are dropped
    input logic [5:0] nt_out,                    // 16 frame tiles per channel in the dest bank
    input logic relu,
    input logic row_valid,                       // one row (one output channel) per cycle from the array or depthwise unit
    input logic [4:0] row_index,
    input tile_tag_t row_tag,
    input logic [FRAMES-1:0][31:0] row_sum,
    output logic [9:0] p_ch,                     // channel to look up in stt_params
    input logic signed [31:0] p_mult, p_shift,   // that channel's numbers -> they arrive PARAM_LAT cycles later
    output logic we,
    output logic [AW-1:0] address,
    output logic [127:0] data
);
    localparam int STAGES = 9;
    logic [STAGES-1:0] valid;
    logic [AW-1:0] address_q[STAGES];
    logic signed [31:0] sum1[FRAMES], sum2[FRAMES], sum3[FRAMES], sum4[FRAMES], mult3, mult4;
    logic signed [63:0] product5[FRAMES], product6[FRAMES], rounded7[FRAMES], q8[FRAMES];
    logic [5:0] n[3:7];
    logic zero[3:7];
    logic [63:0] half[5:6], below[5:6];

    // the output channel is the tile number then the row -> the word goes to channel * nt_out + frame tile
    assign p_ch = {row_tag.otile, row_index};
    assign we = valid[STAGES-1];
    assign address = address_q[STAGES-1];

    // 9 stage pipeline -> wait for the params then multiply then round then shift then clamp
    always_ff @(posedge clk) begin
        valid <= rst ? 0 : {valid[STAGES-2:0], row_valid && p_ch < co};
        address_q[0] <= row_tag.base + row_index * nt_out;
        for (int i = 1; i < STAGES; i++) address_q[i] <= address_q[i-1];
        mult3 <= p_mult;
        mult4 <= mult3;
        n[3] <= 6'(31 + p_shift);
        zero[3] <= p_shift > 32; // a shift that big would leave nothing -> output 0

        for (int i = 4; i <= 7; i++) begin
            n[i] <= n[i-1];
            zero[i] <= zero[i-1];
        end

        // -> half for positive values and half - 1 for negative ones so both round away from zero
        half[5] <= 64'd1 << (n[4] - 1);
        below[5] <= (64'd1 << (n[4] - 1)) - 1;
        half[6] <= half[5];
        below[6] <= below[5];

        for (int l = 0; l < FRAMES; l++) begin
            // -> the sums wait 4 cycles for the params to come out of their ram
            sum1[l] <= row_sum[l];
            sum2[l] <= sum1[l];
            sum3[l] <= sum2[l];
            sum4[l] <= sum3[l];

            product5[l] <= sum4[l] * mult4;
            product6[l] <= product5[l];
            rounded7[l] <= product6[l] + (product6[l] < 0 ? below[6] : half[6]);

            q8[l] <= zero[7] ? 0 : rounded7[l] >>> n[7];

            // -> relu is just clamping at 0
            if (relu) begin
                data[8*l+:8] <= q8[l] < 0 ? 0 : q8[l] > 255 ? 255 : 8'(q8[l]);
            end
            else begin
                data[8*l+:8] <= q8[l] < -128 ? 8'h80 : q8[l] > 127 ? 127 : 8'(q8[l]);
            end
        end
    end
endmodule
