/*
    residual add -> the last layer of each of the 15 blocks
    -> input a is the block's 5 layer path (source bank) and input b is its 1x1 shortcut (branch bank)
    -> the two have different scales so each gets rescaled to int16 on a shared scale before they're summed
    -> the sum clamps to uint8 which is the relu after the add
    -> one byte per cycle with plain logic so no dsps -> only 15 layers so speed doesn't matter much here
*/
module stt_add_ctrl
    import stt_engine_pkg::*;
(
    input logic clk, rst, start,
    input logic [10:0] co,                   // channels
    input logic [5:0] nt,                    // 16 frame tiles per channel
    output logic [AW-1:0] address,           // same word in both input banks and the dest bank
    output logic [9:0] p_ch,                 // channel to look up in stt_params
    input logic [127:0] a_data, b_data,      // 16 frames of a and b
    input logic [3:0][31:0] params,          // multiplier a then shift a then multiplier b then shift b
    output logic we,
    output logic [127:0] data,
    output logic done
);
    typedef enum logic [2:0] {IDLE, WAIT, ISSUE, FLUSH, WRITE, DONE} state_t;
    state_t state;
    logic [5:0] tile;
    logic [3:0] lane, lane1, lane2, lane3;
    logic [2:0] count;
    logic v1, v2, v3;
    logic [5:0] n[2];
    logic zero[2];
    logic [63:0] half[2], below[2];
    (* use_dsp = "no" *) logic signed [38:0] product[2];  // fits in 38 bits
    logic signed [63:0] rounded[2];
    logic signed [15:0] clamped[2];
    logic signed [16:0] sum;
    logic [1:0][127:0] words;

    assign we = state == WRITE;
    assign done = state == DONE;
    assign sum = clamped[0] + clamped[1];
    assign words = {b_data, a_data};

    // NOTE: rescale each input

    for (genvar i = 0; i < 2; i++) begin : g_input
        logic signed [31:0] mult, shift;
        assign mult = params[2*i];
        assign shift = params[2*i+1];
        // multiply then round half away from zero then shift then clamp to int16 -> same maths as the requantiser
        always_ff @(posedge clk) begin
            n[i] <= 6'(31 + shift);
            zero[i] <= shift > 32;
            half[i] <= 64'd1 << (n[i] - 1);
            below[i] <= (64'd1 << (n[i] - 1)) - 1;
            product[i] <= $signed(words[i][8*lane+:8]) * mult;
            rounded[i] <= zero[i] ? 0 : $signed(64'(product[i]) + (product[i] < 0 ? below[i] : half[i])) >>> n[i];
            clamped[i] <= rounded[i] < -32768 ? -32768 : rounded[i] > 32767 ? 32767 : 16'(rounded[i]);
        end
    end

    // NOTE: one word at a time

    // walks every word -> each frame tile of a channel then the next channel
    always_ff @(posedge clk) begin
        v1 <= state == ISSUE;
        v2 <= v1;
        v3 <= v2;
        lane1 <= lane;
        lane2 <= lane1;
        lane3 <= lane2;
        // -> the summed byte lands in its lane 3 cycles after it was issued
        if (v3) begin
            data[8*lane3+:8] <= sum < 0 ? 0 : sum > 255 ? 255 : 8'(sum);
        end
        if (rst) begin
            state <= IDLE;
        end
        else begin
            case (state)
                IDLE: if (start) begin
                    p_ch <= 0;
                    tile <= 0;
                    address <= 0;
                    count <= 0;
                    state <= WAIT;
                end
                // -> give both banks LAT cycles to read the word
                WAIT: begin
                    count <= count + 1;
                    if (count == LAT - 1) begin
                        lane <= 0;
                        state <= ISSUE;
                    end
                end
                // -> one of the 16 frames per cycle
                ISSUE: begin
                    lane <= lane + 1;
                    if (lane == 15) begin
                        count <= 0;
                        state <= FLUSH;
                    end
                end
                // -> let the last byte through the pipeline
                FLUSH: begin
                    count <= count + 1;
                    if (count == 2) begin
                        state <= WRITE;
                    end
                end
                // -> write the word then the bank layout means the next word is just address + 1
                WRITE: begin
                    address <= address + 1;
                    count <= 0;
                    if (tile < nt - 1) begin
                        tile <= tile + 1;
                        state <= WAIT;
                    end
                    else if (p_ch < co - 1) begin
                        tile <= 0;
                        p_ch <= p_ch + 1;
                        state <= WAIT;
                    end
                    else begin
                        state <= DONE;
                    end
                end
                default: ;
            endcase
        end
    end
endmodule
