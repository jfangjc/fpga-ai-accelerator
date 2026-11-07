// Depthwise convolution on stt_dw_array, 16 channels by 16 frames at a time
module stt_dw_ctrl
    import stt_engine_pkg::*;
(
    input logic clk, rst, start,
    input logic [5:0] nt, nt_out,
    input logic [10:0] co,
    input logic [6:0] taps, padding,
    input logic [1:0] stride, dilation,
    input logic [9:0] valid,
    input logic in_signed,
    input logic [1:0] w_full,
    output logic [1:0] w_release,
    output logic w_half,
    output logic [9:0] w_k,
    input logic [255:0] w_rdata,
    output logic [AW - 1:0] act_address,
    input logic [127:0] act_rdata,
    input logic [ROWS - 1:0][31:0] bias,
    output logic row_valid,
    output logic [4:0] row_index,
    output tile_tag_t row_tag,
    output logic [FRAMES - 1:0][31:0] row_sum,
    output logic done
);
    typedef enum logic [3:0] {IDLE, WAIT, LOAD, WEIGHTS, INIT, PRIME, ALIGN, ISSUE, FLUSH, EMIT, NEXT, DONE} state_t;
    state_t state;
    logic [10:0] ch, count;
    logic [5:0] tile;
    logic [6:0] tap;
    logic [3:0] emit, align;
    logic [2:0] prime;
    logic act_re, w_re;
    logic [LAT - 1:0] act_valid, weight_valid;
    logic [6:0] weight_tap[LAT];
    logic [3:0] load_channel, channel_q[LAT];
    logic [5:0] load_tile, tile_q[LAT];
    logic signed [11:0] tile_start, position;
    logic [AW - 1:0] otile_base;
    logic flushed;

    assign act_re = state == LOAD && count < {nt, 4'd0};
    assign w_re = state == WEIGHTS && count < taps;
    assign w_half = ch[5];
    assign w_k = 10'(count);
    assign row_index = 5'(ch) + emit;
    assign row_tag = '{otile: ch[9:5], base: otile_base + AW'(tile)};
    assign row_valid = state == EMIT && ch + emit < co;
    assign done = state == DONE;

    stt_dw_array array(
        clk, act_valid[LAT - 1], channel_q[LAT - 1], tile_q[LAT - 1], act_rdata, weight_valid[LAT - 1],
        weight_tap[LAT - 1], w_rdata, ch[4], state == PRIME, state == ALIGN, state == ISSUE, tap == 0,
        tap == taps - 1, prime, position, tap, stride, dilation, valid, in_signed, bias, emit, flushed, row_sum);

    always_ff @(posedge clk) begin
        act_valid <= {act_valid[LAT - 2:0], act_re};
        weight_valid <= {weight_valid[LAT - 2:0], w_re};
        weight_tap[0] <= 7'(count);
        channel_q[0] <= load_channel;
        tile_q[0] <= load_tile;
        for (int i = 1; i < LAT; i++) begin
            weight_tap[i] <= weight_tap[i - 1];
            channel_q[i] <= channel_q[i - 1];
            tile_q[i] <= tile_q[i - 1];
        end
        if (state == INIT) begin
            position <= tile_start;
        end
        else if (state == ISSUE) begin
            position <= position + dilation;
        end
        w_release <= 0;
        if (rst) begin
            state <= IDLE;
        end
        else begin
            if (act_re) begin
                act_address <= act_address + 1;
                load_tile <= load_tile == nt - 1 ? 0 : load_tile + 1;
                if (load_tile == nt - 1) begin
                    load_channel <= load_channel + 1;
                end
            end
            case (state)
                IDLE: if (start) begin
                    ch <= 0;
                    tile <= 0;
                    tile_start <= -12'(padding);
                    otile_base <= 0;
                    act_address <= 0;
                    load_channel <= 0;
                    load_tile <= 0;
                    state <= WAIT;
                end
                WAIT: if (w_full[w_half]) begin
                    count <= 0;
                    state <= LOAD;
                end
                LOAD: begin
                    count <= count + 1;
                    if (count == {nt, 4'd0} + LAT) begin
                        count <= 0;
                        state <= WEIGHTS;
                    end
                end
                WEIGHTS: begin
                    count <= count + 1;
                    if (count == taps + LAT) begin
                        state <= INIT;
                    end
                end
                INIT: begin
                    tap <= 0;
                    prime <= 0;
                    align <= tile_start[3:0];
                    state <= PRIME;
                end
                PRIME: if (prime == 4) begin
                    state <= align == 0 ? ISSUE : ALIGN;
                end
                else begin
                    prime <= prime + 1;
                end
                ALIGN: begin
                    align <= align - 1;
                    if (align == 1) begin
                        state <= ISSUE;
                    end
                end
                ISSUE: if (tap == taps - 1) begin
                    state <= FLUSH;
                end
                else begin
                    tap <= tap + 1;
                end
                FLUSH: if (flushed) begin
                    emit <= 0;
                    state <= EMIT;
                end
                EMIT: if (emit == 15) begin
                    state <= NEXT;
                end
                else begin
                    emit <= emit + 1;
                end
                NEXT: if (tile < nt_out - 1) begin
                    tile <= tile + 1;
                    tile_start <= tile_start + (12'(stride) << 4);
                    state <= INIT;
                end
                else begin
                    // Group done
                    tile <= 0;
                    tile_start <= -12'(padding);
                    if (ch[4]) begin
                        otile_base <= otile_base + (AW'(nt_out) << 5);
                    end
                    if (ch[4] || ch + 16 >= co) begin
                        w_release <= 2'b01 << w_half;
                    end
                    if (ch + 16 >= co) begin
                        state <= DONE;
                    end
                    else begin
                        ch <= ch + 16;
                        count <= 0;
                        state <= ch[4] ? WAIT : LOAD;
                    end
                end
                default: ;
            endcase
        end
    end
endmodule
