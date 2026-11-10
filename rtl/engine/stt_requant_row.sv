// Output row stage: one drained array row (one output channel, 16 frames)
// per cycle. Reads the channel's multiplier/shift and checks the biased
// accumulator from the array for INT32 overflow
// exactly like golden_int8.checked(), requantizes the 16 lanes in parallel
// and writes the 16 output bytes to word channel*NT + tile of the output
// bank. Rows of channels >= C_out (padding of the last output tile) are
// dropped. There is no backpressure: the output bank always accepts.
module stt_requant_row
    import stt_engine_pkg::*;
(
    input  logic                           clk,
    input  logic                           rst,
    // Shared configuration struct; this module reads only some fields.
    /* verilator lint_off UNUSEDSIGNAL */
    input  layer_cfg_t                     cfg,
    /* verilator lint_on UNUSEDSIGNAL */
    input  logic                           row_valid,
    input  logic [4:0]                     row_idx,
    input  tile_tag_t                      row_tag,
    input  logic [ARRAY_F-1:0][SUM_W-1:0]  row_sum,
    // Parameter RAM read port
    output logic                           p_re,
    output logic [9:0]                     p_ch,
    input  logic [31:0]                    p_mult,
    input  logic [31:0]                    p_shift,
    // Output bank write port
    output logic                           we,
    output logic [ACT_AW-1:0]              waddr,
    output logic [127:0]                   wdata,
    output logic                           overflow,     // sticky
    output logic                           param_error,  // sticky
    output logic                           busy
);
    localparam int D = PARAM_LAT;
    logic [9:0]        ch;
    logic              keep;
    logic [ACT_AW-1:0] addr, addr_q;
    // Delay line while the parameters are read.
    logic                          dv   [D];
    logic [ACT_AW-1:0]             da   [D];
    logic [ARRAY_F-1:0][SUM_W-1:0] ds   [D];
    // Checked accumulator stage feeding the requantizers.
    logic                     av;
    logic [ARRAY_F-1:0][31:0] acc;
    logic [31:0]              amult, ashift;
    logic [ACT_AW-1:0]        aaddr;
    logic                     aovf;
    logic [ARRAY_F-1:0][32:0] wide;
    logic [ARRAY_F-1:0]       lane_ovf;
    // Address delay line matching the requantizer latency.
    logic                     qv [REQUANT_LAT];
    logic [ACT_AW-1:0]        qa [REQUANT_LAT];
    logic [ARRAY_F-1:0]       m_valid, m_error, rq_ready;
    logic signed [31:0]       m_value [ARRAY_F];

    assign ch = {row_tag.otile, row_idx};
    assign keep = row_valid && {1'b0, ch} < cfg.co;
    // Rows of a tile arrive consecutively: row r is at base + r*NT.
    assign addr = cfg.dw ? row_tag.base + ACT_AW'(row_idx)*ACT_AW'(cfg.nt) :
                  row_idx == 5'd0 ? row_tag.base : addr_q + ACT_AW'(cfg.nt);
    assign p_re = keep;
    assign p_ch = ch;

    always_comb begin
        for (int l = 0; l < ARRAY_F; l++) begin
            wide[l] = 33'($signed(ds[D-1][l]));
            lane_ovf[l] = wide[l][32] != wide[l][31];
        end
    end

    always_ff @(posedge clk) begin
        if (row_valid) addr_q <= addr;
        dv[0] <= keep;
        da[0] <= addr;
        ds[0] <= row_sum;
        for (int i = 1; i < D; i++) begin
            dv[i] <= dv[i-1];
            da[i] <= da[i-1];
            ds[i] <= ds[i-1];
        end
        av <= dv[D-1];
        aaddr <= da[D-1];
        amult <= p_mult;
        ashift <= p_shift;
        aovf <= |lane_ovf;
        for (int l = 0; l < ARRAY_F; l++) acc[l] <= wide[l][31:0];
        qv[0] <= av;
        qa[0] <= aaddr;
        for (int i = 1; i < REQUANT_LAT; i++) begin
            qv[i] <= qv[i-1];
            qa[i] <= qa[i-1];
        end
        if (rst) begin
            for (int i = 0; i < D; i++) dv[i] <= 1'b0;
            for (int i = 0; i < REQUANT_LAT; i++) qv[i] <= 1'b0;
            av <= 1'b0;
            overflow <= 1'b0;
            param_error <= 1'b0;
        end else begin
            if (av && aovf) overflow <= 1'b1;
            if (|(m_valid & m_error)) param_error <= 1'b1;
            // Defensive: the requantizer latency must match the delay line.
            // (m_ready is tied high, so the requantizers never stall.)
            if (m_valid != {ARRAY_F{qv[REQUANT_LAT-1]}} || !(&rq_ready)) param_error <= 1'b1;
        end
    end

    for (genvar l = 0; l < ARRAY_F; l++) begin : g_rq
        stt_requant rq (
            .clk, .rst,
            .s_valid(av), .s_ready(rq_ready[l]),
            .s_acc(acc[l]), .s_mult(amult), .s_shift(ashift),
            .s_lo(cfg.relu ? 32'sd0 : -32'sd128),
            .s_hi(cfg.relu ? 32'sd255 : 32'sd127),
            .m_valid(m_valid[l]), .m_ready(1'b1),
            .m_value(m_value[l]), .m_error(m_error[l])
        );
        // Values are clamped to [-128,255]; the low byte is the INT8/UINT8 code.
        assign wdata[8*l +: 8] = m_value[l][7:0];
    end

    assign we = qv[REQUANT_LAT-1];
    assign waddr = qa[REQUANT_LAT-1];

    always_comb begin
        busy = av;
        for (int i = 0; i < D; i++) busy = busy | dv[i];
        for (int i = 0; i < REQUANT_LAT; i++) busy = busy | qv[i];
    end
endmodule


