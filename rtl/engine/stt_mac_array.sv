/*
    32 x 16 int8 mac array -> 512 cells with one dsp each
    -> rows are 32 output channels and columns are 16 frames
    -> each cycle one weight per row goes along the row and one activation per column goes down the column
    -> so every cell does sum[o][t] += w[o][i] * x[i][t] for the same input channel i
    -> the bias goes in as the starting value on the first channel so it costs nothing extra
    -> a finished tile gets copied into a shadow chain and shifted out one row per cycle while the next tile starts
*/
(* use_dsp = "yes" *)
module stt_mac_array
    import stt_engine_pkg::*;
(
    input logic clk, rst,
    input logic in_valid, first, last,           // first and last input channel of a tile
    input tile_tag_t in_tag,                     // where the tile goes in the dest bank
    input logic [ROWS-1:0][7:0] weight,          // one int8 weight per output channel
    input logic [FRAMES-1:0][8:0] activation,    // one 9 bit activation per frame
    input logic [ROWS-1:0][31:0] bias,           // one int32 bias per output channel
    output logic row_valid,                      // high while the 32 rows are going out
    output logic [4:0] row_index,                // which output channel in the tile
    output tile_tag_t row_tag,
    output logic [FRAMES-1:0][31:0] row_sum      // that channel's 16 sums
);
    logic v1, f1, l1;
    (* max_fanout = 32 *) logic v2, f2, l2;
    (* max_fanout = 64 *) logic capture;
    tile_tag_t tag1, tag2, tag3;
    logic [5:0] left;   // rows still to send

    // follows the 3 stage pipeline and counts the 32 rows out after each tile
    always_ff @(posedge clk) begin
        if (rst) begin
            v1 <= 0;
            v2 <= 0;
            capture <= 0;
            left <= 0;
        end
        else begin
            v1 <= in_valid;
            v2 <= v1;
            // -> the last channel just got added so the sums are final
            capture <= v2 && l2;
            if (capture) begin
                left <= ROWS;
            end
            else if (left != 0) begin
                left <= left - 1;
            end
        end
        f1 <= first;
        f2 <= f1;
        l1 <= last;
        l2 <= l1;
        tag1 <= in_tag;
        tag2 <= tag1;
        tag3 <= tag2;
        if (capture) begin
            row_tag <= tag3;
            row_index <= 0;
        end
        else if (left != 0) begin
            row_index <= row_index + 1;
        end
    end

    // NOTE: the 512 cells

    for (genvar t = 0; t < FRAMES; t++) begin : g_frame
        logic signed [31:0] sum[ROWS], shadow[ROWS];
        for (genvar o = 0; o < ROWS; o++) begin : g_cell
            logic signed [8:0] a1;
            logic signed [7:0] b1;
            logic signed [31:0] m2, bias1, bias2;
            // one cell -> register the inputs then multiply then accumulate
            always_ff @(posedge clk) begin
                if (in_valid) begin
                    bias1 <= bias[o];
                    a1 <= activation[t];
                    b1 <= weight[o];
                end
                // -> activation x weight
                if (v1) begin
                    bias2 <= bias1;
                    m2 <= a1 * b1;
                end
                // -> the first channel starts from the bias and the rest add on
                if (v2) begin
                    sum[o] <= (f2 ? bias2 : sum[o]) + m2;
                end
                // -> copy the finished sums out then shift them toward row 0 one row per cycle
                shadow[o] <= capture ? sum[o] : shadow[(o + 1) % ROWS];
            end
        end
        assign row_sum[t] = shadow[0];
    end

    assign row_valid = left != 0;
endmodule
