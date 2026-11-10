// Pointwise compute controller. For each 32-channel output tile j it waits
// until weight half j%2 is full, then for every 16-frame tile f issues Ci
// reduction terms k: activation word k*NT+f and weight row k of half j%2.
// Terms are issued back to back across frame tiles; a frame tile with
// Ci < 32 is padded to 32 cycles so the accumulator drain (32 rows) is
// never overrun. After the last frame tile the half is released so the
// parser can load tile j+2 while tile j+1 computes.
//
// Read data returns RD_LAT cycles after issue; the control bits travel in a
// matching delay line and the operands are registered (with INT8 sign or
// UINT8 zero extension to nine bits) before broadcast into the array.
module stt_pw_ctrl
    import stt_engine_pkg::*;
#(
    parameter int RD_LAT = ACT_LAT
) (
    input  logic                    clk,
    input  logic                    rst,
    input  logic                    start,
    // Shared configuration struct; this module reads only some fields.
    /* verilator lint_off UNUSEDSIGNAL */
    input  layer_cfg_t              cfg,
    /* verilator lint_on UNUSEDSIGNAL */
    // Weight ping-pong buffer
    input  logic [1:0]              w_full,
    output logic [1:0]              w_release,
    output logic                    w_re,
    output logic                    w_half,
    output logic [9:0]              w_k,
    input  logic [255:0]            w_rdata,
    // Input activation bank
    output logic                    act_re,
    output logic [ACT_AW-1:0]       act_raddr,
    input  logic [127:0]            act_rdata,
    // Array operands (registered)
    output logic                    a_valid,
    output logic                    a_first,
    output logic                    a_last,
    output tile_tag_t               a_tag,
    output logic [ARRAY_O-1:0][7:0] a_weight,
    output logic [ARRAY_F-1:0][8:0] a_act,
    output logic                    finished,     // all terms issued and returned
    output logic                    busy,
    output logic weight_wait,
    output logic [31:0]             issue_cycles  // terms issued this command
);
    typedef enum logic [2:0] {C_IDLE, C_WAIT, C_RUN, C_PAD, C_DONE} cstate_t;
    cstate_t state;
    logic [4:0]        j;        // output tile
    logic              h;        // weight half of tile j
    logic [5:0]        f;        // frame tile
    logic [9:0]        k;        // reduction term
    logic [ACT_AW-1:0] addr;     // k*NT + f
    logic [ACT_AW-1:0] obase;    // j*32*NT
    logic [4:0]        pad;
    logic              issue, first, last, tile_done;
    // Delay line matching the RAM read latency.
    logic [RD_LAT-1:0] dv, df, dl;
    tile_tag_t         dtag [RD_LAT];
    logic [5:0] frame_delay [RD_LAT];

    assign weight_wait=state==C_WAIT && !w_full[h];
    assign issue = state == C_RUN;
    assign first = k == 10'd0;
    assign last = k == 10'(cfg.ci - 11'd1);
    assign act_re = issue;
    assign act_raddr = addr;
    assign w_re = issue;
    assign w_half = h;
    assign w_k = k;
    // The last term of a frame tile ends it unless padding follows.
    assign tile_done = (state == C_RUN && last && cfg.ci >= 11'd32)
                    || (state == C_PAD && pad == 5'd0);

    always_ff @(posedge clk) begin
        w_release <= 2'b00;
        if (rst) begin
            state <= C_IDLE;
            issue_cycles <= '0;
        end else begin
            if (issue) issue_cycles <= issue_cycles + 32'd1;
            case (state)
                C_IDLE: if (start) begin
                    j <= '0;
                    h <= 1'b0;
                    obase <= '0;
                    state <= C_WAIT;
                end
                C_WAIT: if (w_full[h]) begin
                    f <= '0;
                    k <= '0;
                    addr <= '0;
                    state <= C_RUN;
                end
                C_RUN: begin
                    if (!last) begin
                        k <= k + 10'd1;
                        addr <= addr + ACT_AW'(cfg.nt);
                    end else if (cfg.ci < 11'd32) begin
                        pad <= 5'(11'd31 - cfg.ci);
                        state <= C_PAD;
                    end
                end
                C_PAD: pad <= pad - 5'd1;
                default: ;
            endcase
            if (tile_done) begin
                k <= '0;
                if (f == cfg.nt - 6'd1) begin
                    // Output tile complete: hand its weight half back.
                    w_release <= h ? 2'b10 : 2'b01;
                    h <= !h;
                    j <= j + 5'd1;
                    obase <= obase + (ACT_AW'(cfg.nt) << 5);
                    state <= (j == 5'(cfg.n_otile - 6'd1)) ? C_DONE : C_WAIT;
                end else begin
                    f <= f + 6'd1;
                    addr <= ACT_AW'(f) + 1'b1;
                    state <= C_RUN;
                end
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            dv <= '0;
            a_valid <= 1'b0;
        end else begin
            dv <= {dv[RD_LAT-2:0], issue};
            a_valid <= dv[RD_LAT-1];
        end
        df <= {df[RD_LAT-2:0], first};
        dl <= {dl[RD_LAT-2:0], last};
        frame_delay[0] <= f;
        for (int i = 1; i < RD_LAT; i++) frame_delay[i] <= frame_delay[i-1];
        dtag[0] <= '{otile: j, base: obase + ACT_AW'(f)};
        for (int i = 1; i < RD_LAT; i++) dtag[i] <= dtag[i-1];
        a_first <= df[RD_LAT-1];
        a_last <= dl[RD_LAT-1];
        a_tag <= dtag[RD_LAT-1];
        for (int o = 0; o < ARRAY_O; o++) a_weight[o] <= w_rdata[8*o +: 8];
        for (int t = 0; t < ARRAY_F; t++)
            a_act[t] <= (32'(frame_delay[RD_LAT-1])*16+32'(t) < 32'(cfg.valid))
                ? {cfg.in_signed && act_rdata[8*t+7], act_rdata[8*t +: 8]} : 9'd0;
    end

    assign finished = state == C_DONE && dv == '0 && !a_valid;
    assign busy = (state != C_IDLE && state != C_DONE) || dv != '0 || a_valid;
endmodule
