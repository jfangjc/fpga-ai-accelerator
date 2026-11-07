// Depthwise MACs WITH 16 channels by 16 frames, 256 MACs per cycle
module stt_dw_array
    import stt_engine_pkg::*;
(
    input logic clk,
    input logic act_we,
    input logic [3:0] act_channel,
    input logic [5:0] act_tile,
    input logic [127:0] act_wdata,
    input logic w_we,
    input logic [6:0] w_tap,
    input logic [255:0] w_rdata,
    input logic lane,
    input logic prime, align, issue, first, last,
    input logic [2:0] prime_count,
    input logic signed [11:0] position,
    input logic [6:0] tap,
    input logic [1:0] stride, dilation,
    input logic [9:0] valid,
    input logic in_signed,
    input logic [ROWS - 1:0][31:0] bias,
    input logic [3:0] emit,
    output logic flushed,
    output logic [FRAMES - 1:0][31:0] row_sum
);
    logic signed [11:0] position_q;
    logic [255:0] gather[16];
    logic signed [7:0] gather_weight[16], weight_q[16];
    (* use_dsp = "yes" *) logic signed [31:0] acc[16][16];
    logic v0, v1, v2, first0, first1, first2, last0, last1;

    for (genvar f = 0; f < FRAMES; f++) begin : g_sum
        assign row_sum[f] = acc[emit][f];
    end

    always_ff @(posedge clk) begin
        position_q <= position;
        v0 <= issue;
        v1 <= v0;
        v2 <= v1;
        first0 <= issue && first;
        first1 <= first0;
        first2 <= first1;
        last0 <= issue && last;
        last1 <= last0;
        flushed <= last1;
    end

    for (genvar c = 0; c < 16; c++) begin : g_channel
        stt_dw_window window(
            clk, act_we && act_channel == c, act_tile, act_wdata, prime, align, issue, prime_count, position[9:0],
            dilation, gather[c]);
        logic [7:0] weights[87];
        always_ff @(posedge clk) begin
            if (w_we) begin
                weights[w_tap] <= w_rdata[128 * lane + 8 * c+:8];
            end
            if (issue) begin
                gather_weight[c] <= weights[tap];
            end
            if (v0) begin
                weight_q[c] <= gather_weight[c];
            end
        end
        for (genvar f = 0; f < FRAMES; f++) begin : g_frame
            logic signed [11:0] at;
            logic [7:0] sample;
            logic signed [8:0] operand;
            logic signed [31:0] bias1, bias2;
            (* use_dsp = "yes" *) logic signed [31:0] product;
            assign at = position_q + 12'(f) * stride;
            assign sample = stride == 2 ? gather[c][16 * f+:8] : gather[c][8 * f+:8];
            always_ff @(posedge clk) begin
                if (v0) begin
                    bias1 <= bias[{lane, 4'(c)}];
                    operand <= at >= 0 && at < valid ? {in_signed && sample[7], sample} : 0;
                end
                if (v1) begin
                    bias2 <= bias1;
                    product <= operand * weight_q[c];
                end
                if (v2) begin
                    acc[c][f] <= (first2 ? bias2 : acc[c][f]) + product;
                end
            end
        end
    end
endmodule
