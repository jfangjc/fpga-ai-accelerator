/*
    pointwise convolution on the mac array
    -> a pointwise layer is a matrix multiply -> out[o][t] = bias[o] + sum over i of w[o][i] * x[i][t]
    -> the array does 32 output channels x 16 frames at a time and takes one input channel per cycle
    -> loop order is output tile -> frame tile -> input channel so each 32 row weight tile is reused for every frame tile
    -> that's why every weight only comes from ddr once per run
*/
module stt_pw_ctrl
    import stt_engine_pkg::*;
(
    input logic clk, rst, start,             // start is a one cycle pulse once the layer's params are in
    input logic [10:0] ci,                   // input channels -> cycles per tile
    input logic [5:0] nt, n_otile,           // 16 frame tiles and 32 channel output tiles
    input logic [9:0] valid,                 // real input frames -> frames past this read as 0
    input logic in_signed,                   // input is int8 instead of uint8
    input logic [1:0] w_full,                // which weight buffer halves hold a whole tile
    output logic [1:0] w_release,            // one cycle pulse to hand a half back for the next tile
    output logic w_half,                     // half being read
    output logic [9:0] w_k,                  // weight row being read -> one row per input channel
    input logic [255:0] w_rdata,             // 32 int8 weights -> one per output channel
    output logic [AW-1:0] act_address,       // source bank word -> input channel k for this frame tile
    input logic [127:0] act_rdata,           // 16 frames of that channel
    output logic a_valid, a_first, a_last,   // to the mac array lined up with the data
    output tile_tag_t a_tag,                 // where the finished tile goes in the dest bank
    output logic [ROWS-1:0][7:0] a_weight,
    output logic [FRAMES-1:0][8:0] a_act,    // 9 bits so one signed multiplier covers int8 and uint8
    output logic done
);
    typedef enum logic [2:0] {IDLE, WAIT, RUN, PAD, DONE} state_t;
    state_t state;
    logic [4:0] otile, pad;
    logic [5:0] tile;
    logic [9:0] k;
    logic [AW-1:0] obase;  // output tile's first word in the dest bank
    logic issue, first, last, tile_done;
    logic [LAT-1:0] dv, df, dl;
    tile_tag_t dtag[LAT];
    logic [5:0] dtile[LAT];

    // a tile is done after its last input channel -> layers with under 32 inputs pad out to 32 cycles
    // so the array has time to shift the last tile's 32 rows out before the next one finishes
    assign issue = state == RUN;
    assign w_k = k;
    assign first = k == 0;
    assign last = k == ci - 1;
    assign tile_done = state == RUN && last && ci >= 32 || state == PAD && pad == 0;
    assign done = state == DONE;

    // NOTE: tile loop

    // steps through input channels then frame tiles then output tiles
    always_ff @(posedge clk) begin
        w_release <= 0;
        if (rst) begin
            state <= IDLE;
        end
        else begin
            case (state)
                IDLE: if (start) begin
                    otile <= 0;
                    w_half <= 0;
                    obase <= 0;
                    state <= WAIT;
                end
                // -> wait for the dma to fill this output tile's weights
                WAIT: if (w_full[w_half]) begin
                    tile <= 0;
                    k <= 0;
                    act_address <= 0;
                    state <= RUN;
                end
                // -> one input channel per cycle -> the next channel's word for this frame tile is nt words on
                RUN: begin
                    if (!last) begin
                        k <= k + 1;
                        act_address <= act_address + nt;
                    end
                    else if (ci < 32) begin
                        pad <= 5'(31 - ci);
                        state <= PAD;
                    end
                end
                PAD: pad <= pad - 1;
                default: ;
            endcase
            if (tile_done) begin
                k <= 0;
                // -> last frame tile -> hand the weight half back so the dma can fill it with the tile after next
                if (tile == nt - 1) begin
                    w_release <= 2'b01 << w_half;
                    w_half <= !w_half;
                    otile <= otile + 1;
                    obase <= obase + (AW'(nt) << 5);
                    state <= otile == n_otile - 1 ? DONE : WAIT;
                end
                // -> same weights on the next 16 frames -> back to channel 0 of the next frame tile
                else begin
                    tile <= tile + 1;
                    act_address <= AW'(tile) + 1;
                    state <= RUN;
                end
            end
        end
    end

    // NOTE: line up with the memories

    // delays the control by LAT cycles so it meets the bank and weight data coming out of the rams
    always_ff @(posedge clk) begin
        if (rst) begin
            dv <= 0;
            a_valid <= 0;
        end
        else begin
            dv <= {dv[LAT-2:0], issue};
            a_valid <= dv[LAT-1];
        end

        df <= {df[LAT-2:0], first};
        dl <= {dl[LAT-2:0], last};

        dtag[0] <= '{otile: otile, base: obase + AW'(tile)};
        dtile[0] <= tile;

        for (int i = 1; i < LAT; i++) begin
            dtag[i] <= dtag[i-1];
            dtile[i] <= dtile[i-1];
        end

        a_first <= df[LAT-1];
        a_last <= dl[LAT-1];
        a_tag <= dtag[LAT-1];
        a_weight <= w_rdata;

        // -> padding frames past valid become 0 and int8 inputs get sign extended to 9 bits
        for (int t = 0; t < FRAMES; t++)
            a_act[t] <= {dtile[LAT-1], 4'(t)} < valid ? {in_signed && act_rdata[8*t+7], act_rdata[8*t+:8]} : 0;
    end
endmodule
