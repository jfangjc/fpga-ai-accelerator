// Pointwise convolution on stt_pw_array
module stt_pw_ctrl
    import stt_engine_pkg::*;
(
    input logic clk, rst, start,
    input logic [10:0] ci,
    input logic [5:0] nt, n_otile,
    input logic [9:0] valid,
    input logic in_signed,
    input logic [1:0] w_full,
    output logic [1:0] w_release,
    output logic w_half,
    output logic [9:0] w_k,
    input logic [255:0] w_rdata,
    output logic [AW - 1:0] act_address,
    input logic [127:0] act_rdata,
    output logic a_valid, a_first, a_last,
    output tile_tag_t a_tag,
    output logic [ROWS - 1:0][7:0] a_weight,
    output logic [FRAMES - 1:0][8:0] a_act,
    output logic done
);
    typedef enum logic [2:0] {IDLE, WAIT, RUN, PAD, DONE} state_t;
    state_t state;
    logic [4:0] otile, pad;
    logic [5:0] tile;
    logic [9:0] k;
    logic [AW - 1:0] obase;  // output tile's first word
    logic issue, first, last, tile_done;
    logic [LAT - 1:0] dv, df, dl;
    tile_tag_t dtag[LAT];
    logic [5:0] dtile[LAT];

    assign issue = state == RUN;
    assign w_k = k;
    assign first = k == 0;
    assign last = k == ci - 1;
    assign tile_done = state == RUN && last && ci >= 32 || state == PAD && pad == 0;
    assign done = state == DONE;

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
                WAIT: if (w_full[w_half]) begin
                    tile <= 0;
                    k <= 0;
                    act_address <= 0;
                    state <= RUN;
                end
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
                if (tile == nt - 1) begin
                    w_release <= 2'b01 << w_half;
                    w_half <= !w_half;
                    otile <= otile + 1;
                    obase <= obase + (AW'(nt) << 5);
                    state <= otile == n_otile - 1 ? DONE : WAIT;
                end
                else begin
                    tile <= tile + 1;
                    act_address <= AW'(tile) + 1;
                    state <= RUN;
                end
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            dv <= 0;
            a_valid <= 0;
        end
        else begin
            dv <= {dv[LAT - 2:0], issue};
            a_valid <= dv[LAT - 1];
        end
        df <= {df[LAT - 2:0], first};
        dl <= {dl[LAT - 2:0], last};
        dtag[0] <= '{otile: otile, base: obase + AW'(tile)};
        dtile[0] <= tile;
        for (int i = 1; i < LAT; i++) begin
            dtag[i] <= dtag[i - 1];
            dtile[i] <= dtile[i - 1];
        end
        a_first <= df[LAT - 1];
        a_last <= dl[LAT - 1];
        a_tag <= dtag[LAT - 1];
        a_weight <= w_rdata;
        for (int t = 0; t < FRAMES; t++) begin
            a_act[t] <= {dtile[LAT - 1], 4'(t)} < valid ? {in_signed && act_rdata[8 * t + 7], act_rdata[8 * t+:8]} : 0;
        end

    end
endmodule
