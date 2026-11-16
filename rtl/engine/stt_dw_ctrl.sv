/*
    depthwise convolution -> 16 channels x 16 frames at a time
    -> each channel has its own filter along time -> out[c][t] = bias[c] + sum over k of w[c][k] * x[c][s*t + d*k - p]
    -> there's no mixing between channels so it needs far fewer weights than a full convolution
    -> one tap per cycle so a tile takes taps cycles (33 to 87) on 256 macs
    -> each channel of the group gets its input copied into its own line buffer (stt_dw_window) once
    -> then every output tile slides along that buffer so the bank only gets read once per group
*/
module stt_dw_ctrl
    import stt_engine_pkg::*;
(
    input logic clk, rst, start,             // start is a one cycle pulse once the layer's params are in
    input logic [5:0] nt, nt_out,            // 16 frame tiles in and out (stride 2 halves them)
    input logic [10:0] co,                   // channels
    input logic [6:0] taps, padding,
    input logic [1:0] stride, dilation,      // stride 2 only in layer 0 and dilation 2 only in layer 182
    input logic [9:0] valid,                 // real input frames -> anything outside reads as 0
    input logic in_signed,
    input logic [1:0] w_full,
    output logic [1:0] w_release,
    output logic w_half,                     // one weight half holds 32 channels -> two groups of 16
    output logic [9:0] w_k,                  // weight row -> one row per tap
    input logic [255:0] w_rdata,
    output logic [AW-1:0] act_address,
    input logic [127:0] act_rdata,
    input logic [ROWS-1:0][31:0] bias,
    output logic row_valid,                  // one channel's 16 sums per cycle to the requantiser
    output logic [4:0] row_index,
    output tile_tag_t row_tag,
    output logic [FRAMES-1:0][31:0] row_sum,
    output logic done
);
    typedef enum logic [3:0] {IDLE, WAIT, LOAD, WEIGHTS, INIT, PRIME, ALIGN, ISSUE, FLUSH, EMIT, NEXT, DONE} state_t;
    state_t state;
    logic [10:0] ch, count;  // group's first channel and the load counter
    logic [5:0] tile;        // output tile
    logic [6:0] tap;
    logic [3:0] emit, align;
    logic [2:0] prime;
    logic act_re, w_re;
    logic [LAT-1:0] act_valid, weight_valid;
    logic [6:0] weight_tap[LAT];
    logic [3:0] load_channel, channel_q[LAT];
    logic [5:0] load_tile, tile_q[LAT];
    logic signed [11:0] tile_start, position, position_q;   // input frame the current output tile and tap start at
    logic [AW-1:0] otile_base;
    logic [255:0] gather[16];
    logic signed [7:0] gather_weight[16], weight_q[16];
    (* use_dsp = "yes" *) logic signed [31:0] acc[16][16];
    logic v0, v1, v2, first0, first1, first2, last0, last1, last2;

    // reads the bank while loading and the weight buffer while fetching taps -> emits one channel per cycle
    assign act_re = state == LOAD && count < {nt, 4'd0};
    assign w_re = state == WEIGHTS && count < taps;
    assign w_half = ch[5];
    assign w_k = 10'(count);
    assign row_index = 5'(ch) + emit;
    assign row_tag = '{otile: ch[9:5], base: otile_base + AW'(tile)};
    assign row_valid = state == EMIT && ch + emit < co;
    assign done = state == DONE;
    for (genvar f = 0; f < FRAMES; f++) begin : g_sum
        assign row_sum[f] = acc[emit][f];
    end

    // NOTE: the 256 macs (one tap per cycle)

    for (genvar c = 0; c < 16; c++) begin : g_channel
        // this channel's line buffer -> hands back 32 input frames per tap
        stt_dw_window window(
            clk, act_valid[LAT-1] && channel_q[LAT-1] == c, tile_q[LAT-1], act_rdata, state == PRIME,
            state == ALIGN, state == ISSUE, prime, position[9:0], dilation, gather[c]);
        logic [7:0] weights[87];
        // keeps this channel's taps then picks the one for this cycle
        always_ff @(posedge clk) begin
            // -> a weight row has 32 channels so ch[4] picks which 16 belong to this group
            if (weight_valid[LAT-1]) begin
                weights[weight_tap[LAT-1]] <= w_rdata[128*ch[4]+8*c+:8];
            end
            if (state == ISSUE) begin
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
            // the input frame this output frame needs at this tap -> stride 2 takes every other byte
            assign at = position_q + 12'(f) * stride;
            assign sample = stride == 2 ? gather[c][16*f+:8] : gather[c][8*f+:8];
            // one mac -> zero the padding then multiply then accumulate
            always_ff @(posedge clk) begin
                // -> frames before 0 or past valid are the zero padding at the ends
                if (v0) begin
                    bias1 <= bias[{ch[4], 4'(c)}];
                    operand <= at >= 0 && at < valid ? {in_signed && sample[7], sample} : 0;
                end
                if (v1) begin
                    bias2 <= bias1;
                    product <= operand * weight_q[c];
                end
                // -> the first tap starts from the bias
                if (v2) begin
                    acc[c][f] <= (first2 ? bias2 : acc[c][f]) + product;
                end
            end
        end
    end

    // NOTE: control

    // delays the memory reads by LAT and steps through load -> taps -> tiles -> groups
    always_ff @(posedge clk) begin
        act_valid <= {act_valid[LAT-2:0], act_re};
        weight_valid <= {weight_valid[LAT-2:0], w_re};
        weight_tap[0] <= 7'(count);
        channel_q[0] <= load_channel;
        tile_q[0] <= load_tile;
        for (int i = 1; i < LAT; i++) begin
            weight_tap[i] <= weight_tap[i-1];
            channel_q[i] <= channel_q[i-1];
            tile_q[i] <= tile_q[i-1];
        end
        // -> each tap moves dilation frames further along the input
        if (state == INIT) begin
            position <= tile_start;
        end
        else if (state == ISSUE) begin
            position <= position + dilation;
        end
        position_q <= position;
        v0 <= state == ISSUE;
        v1 <= v0;
        v2 <= v1;
        first0 <= state == ISSUE && tap == 0;
        first1 <= first0;
        first2 <= first1;
        last0 <= state == ISSUE && tap == taps - 1;
        last1 <= last0;
        last2 <= last1;
        w_release <= 0;
        if (rst) begin
            state <= IDLE;
        end
        else begin
            // -> loading walks the group's channels in bank order -> every tile of one channel then the next channel
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
                // -> wait for this group's 32 channel weight tile
                WAIT: if (w_full[w_half]) begin
                    count <= 0;
                    state <= LOAD;
                end
                // -> copy all 16 channels' input into their line buffers
                LOAD: begin
                    count <= count + 1;
                    if (count == {nt, 4'd0} + LAT) begin
                        count <= 0;
                        state <= WEIGHTS;
                    end
                end
                // -> copy every tap's weight for these 16 channels
                WEIGHTS: begin
                    count <= count + 1;
                    if (count == taps + LAT) begin
                        state <= INIT;
                    end
                end
                // -> start an output tile at its first input frame (negative means it starts in the padding)
                INIT: begin
                    tap <= 0;
                    prime <= 0;
                    align <= tile_start[3:0];
                    state <= PRIME;
                end
                // -> fill the line buffer's window with 3 words
                PRIME: if (prime == 4) begin
                    state <= align == 0 ? ISSUE : ALIGN;
                end
                else begin
                    prime <= prime + 1;
                end
                // -> shift the window byte by byte so it starts on the exact frame
                ALIGN: begin
                    align <= align - 1;
                    if (align == 1) begin
                        state <= ISSUE;
                    end
                end
                // -> one tap per cycle on all 256 macs
                ISSUE: if (tap == taps - 1) begin
                    state <= FLUSH;
                end
                else begin
                    tap <= tap + 1;
                end
                // -> wait for the last tap to reach the accumulators
                FLUSH: if (last2) begin
                    emit <= 0;
                    state <= EMIT;
                end
                // -> send the 16 channels to the requantiser one per cycle
                EMIT: if (emit == 15) begin
                    state <= NEXT;
                end
                else begin
                    emit <= emit + 1;
                end
                // -> next output tile starts 16 output frames on -> 16 * stride input frames
                NEXT: if (tile < nt_out - 1) begin
                    tile <= tile + 1;
                    tile_start <= tile_start + (12'(stride) << 4);
                    state <= INIT;
                end
                else begin
                    // -> group done so move to the next 16 channels
                    tile <= 0;
                    tile_start <= -12'(padding);
                    // -> after the second group of 16 the 32 channel output tile is full so its base moves on
                    if (ch[4]) begin
                        otile_base <= otile_base + (AW'(nt_out) << 5);
                    end
                    // -> both groups have used this weight half so hand it back
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
