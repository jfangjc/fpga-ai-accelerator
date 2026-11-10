// stt_engine: QNC2 integer layer engine: pointwise, depthwise and residual add.
//
//   s_axis (MM2S) -> stt_cmd_parser -+-> stt_act_unpack -> input bank (URAM)
//                                    +-> stt_param_ram (bias/mult/shift)
//                                    +-> stt_weight_buf (ping-pong tiles)
//   stt_pw_ctrl -> stt_mac_array (32x16 DSP MACs) -> stt_acc_drain
//     -> stt_requant_row (16 x stt_requant) -> output bank (URAM)
//   stt_resp_packer -> m_axis (S2MM)        stt_axil_regs -> irq
//
// One command is processed at a time: parse (weights of the next output
// tile load while the current tile computes), compute, then respond with
// the QNS2 header followed by the outputs. Outputs are buffered in the
// output bank so the header can carry the final status and cycle count.
// After each response the datapath is reset for one cycle (clean), so an
// aborted command leaves no state behind. Single clock, synchronous
// active-low reset; CONTROL.SOFT_RESET resets everything but the AXI-Lite
// settings and must be paired with DMA channel resets.
module stt_engine
    import stt_engine_pkg::*;
#(parameter int DW_FRAME_LANES = 16) (
    input  logic         aclk,
    input  logic         aresetn,
    // AXI4-Lite control slave
    input  logic [7:0]   s_axil_awaddr,
    input  logic         s_axil_awvalid,
    output logic         s_axil_awready,
    input  logic [31:0]  s_axil_wdata,
    input  logic [3:0]   s_axil_wstrb,
    input  logic         s_axil_wvalid,
    output logic         s_axil_wready,
    output logic [1:0]   s_axil_bresp,
    output logic         s_axil_bvalid,
    input  logic         s_axil_bready,
    input  logic [7:0]   s_axil_araddr,
    input  logic         s_axil_arvalid,
    output logic         s_axil_arready,
    output logic [31:0]  s_axil_rdata,
    output logic [1:0]   s_axil_rresp,
    output logic         s_axil_rvalid,
    input  logic         s_axil_rready,
    // Command stream from AXI DMA MM2S
    input  logic [127:0] s_axis_tdata,
    input  logic [15:0]  s_axis_tkeep,
    input  logic         s_axis_tvalid,
    output logic         s_axis_tready,
    input  logic         s_axis_tlast,
    // Response stream to AXI DMA S2MM
    output logic [127:0] m_axis_tdata,
    output logic [15:0]  m_axis_tkeep,
    output logic         m_axis_tvalid,
    input  logic         m_axis_tready,
    output logic         m_axis_tlast,
    // Level interrupt
    output logic         irq
);
    typedef enum logic [1:0] {E_IDLE, E_RUN, E_RESP, E_CLEAN} estate_t;
    localparam int TAG_W = $bits(tile_tag_t);

    logic branch, add_p_we, add_re, add_we, add_done;
    logic [ACT_AW-1:0] add_addr;
    logic [127:0] add_data;
    logic rq_we;
    logic [ACT_AW-1:0] rq_addr;
    logic [127:0] rq_data;
    layer_cfg_t unpack_cfg;
    always_comb begin
        unpack_cfg=cfg;
        if (cfg.add) unpack_cfg.valid=cfg.t;
    end
    stt_add_ctrl add_ctrl (
        .clk(aclk), .rst(layer_rst), .start(ctrl_start && cfg.add), .unused_cfg(cfg),
        .p_we(add_p_we), .p_sel(pr_sel), .p_addr(pr_addr), .p_data(s_axis_tdata),
        .re(add_re), .addr(add_addr), .a_data(ib_rdata), .b_data(ob_rdata),
        .we(add_we), .data(add_data), .done(add_done)
    );
    assign ob_we = cfg.add ? (add_we || (ib_we && branch)) : rq_we;
    assign ob_waddr = cfg.add ? (add_we ? add_addr : ib_waddr) : rq_addr;
    assign ob_wdata = cfg.add ? (add_we ? add_data : ib_wdata) : rq_data;
    layer_cfg_t out_cfg;
    always_comb begin
        out_cfg=cfg;
        out_cfg.t=cfg.tout; out_cfg.valid=cfg.vout;
        out_cfg.nt=6'((32'(cfg.tout)+15)/16);
        out_cfg.last_n=5'((32'(cfg.tout)-1)%16+1);
    end
    logic dw_done, dw_busy, dw_overflow, dw_weight_wait, pw_weight_wait;
    logic [31:0] dw_cycles, pw_cycles;
    logic dw_ar, pw_ar, dw_wr, pw_wr, dw_wh, pw_wh;
    logic [ACT_AW-1:0] dw_addr, pw_addr;
    logic [9:0] dw_k, pw_k;
    logic [1:0] dw_rel, pw_rel;
    logic dw_rv, pw_rv;
    logic [4:0] dw_ri, pw_ri;
    tile_tag_t dw_tag, pw_tag;
    logic [15:0][SUM_W-1:0] dw_sum, pw_sum;
    assign ib_re=cfg.add ? add_re : cfg.dw ? dw_ar : pw_ar;
    assign ib_raddr=cfg.add ? add_addr : cfg.dw ? dw_addr : pw_addr;
    assign w_re=cfg.dw ? dw_wr : pw_wr;
    assign w_half=cfg.dw ? dw_wh : pw_wh;
    assign w_k=cfg.dw ? dw_k : pw_k;
    assign w_release=cfg.dw ? dw_rel : pw_rel;
    assign row_valid=cfg.dw ? dw_rv : pw_rv;
    assign row_idx=cfg.dw ? dw_ri : pw_ri;
    assign row_tag=cfg.dw ? dw_tag : pw_tag;
    assign row_sum=cfg.dw ? dw_sum : pw_sum;
    assign issue_cycles=cfg.dw ? dw_cycles : pw_cycles;
    stt_dw_ctrl #(.FRAME_LANES(DW_FRAME_LANES)) dw (
        .clk(aclk), .rst(layer_rst), .start(ctrl_start && cfg.dw), .cfg,
        .w_full, .w_release(dw_rel), .w_re(dw_wr), .w_half(dw_wh), .w_k(dw_k), .w_rdata,
        .act_re(dw_ar), .act_raddr(dw_addr), .act_rdata(ib_rdata), .bias(tile_bias),
        .row_tag(dw_tag), .row_idx(dw_ri), .row_valid(dw_rv), .row_sum(dw_sum),
        .finished(dw_done), .busy(dw_busy), .overflow(dw_overflow), .issue_cycles(dw_cycles), .weight_wait(dw_weight_wait)
    );
    // Resets
    logic soft_reset, rst, dp_rst, layer_rst;
    logic network_mode, layer_reset, layer_ready;
    logic [2:0] source_bank, branch_bank, dest_bank;
    logic [31:0] total_issues;
    assign layer_rst=dp_rst || layer_reset;
    // Parser
    layer_cfg_t  cfg;
    logic [31:0] p_opcode, p_tag, p_err_beat;
    logic [3:0]  p_status;
    logic        p_done, params_done, unpack_start, in_valid, in_ready;
    logic        pr_we, w_valid, w_ready;
    logic [1:0]  pr_sel;
    logic [7:0]  pr_addr;
    // Input bank
    logic              ib_we, ib_re, unpack_done;
    logic [ACT_AW-1:0] ib_waddr, ib_raddr;
    logic [127:0]      ib_wdata, ib_rdata;
    // Weights and parameters
    logic [1:0]   w_full, w_release;
    logic         w_re, w_half;
    logic [9:0]   w_k;
    logic [255:0] w_rdata;
    logic         pr_re;
    logic [9:0]   pr_ch;
    logic [31:0]  unused_pr_bias, pr_mult, pr_shift;
    logic [31:0][31:0] tile_bias;
    logic array_overflow;
    // Array path
    logic                           ctrl_start, ctrl_finished, ctrl_busy;
    logic [31:0]                    issue_cycles;
    logic                           a_valid, a_first, a_last;
    tile_tag_t                      a_tag, cap_tag, row_tag;
    logic [ARRAY_O-1:0][7:0]        a_weight;
    logic [ARRAY_F-1:0][8:0]        a_act;
    logic                           cap_valid, arr_busy;
    logic [ARRAY_O-1:0][ARRAY_F-1:0] cap_cell;   // replicated capture strobes
    logic [ARRAY_O-1:0][ARRAY_F-1:0][SUM_W-1:0] sums;
    logic                           row_valid, drain_busy, collision;
    logic [4:0]                     row_idx;
    logic [ARRAY_F-1:0][SUM_W-1:0]  row_sum;
    logic                           rq_busy, overflow, param_error;
    // Output bank
    logic              ob_we, ob_re;
    logic [ACT_AW-1:0] ob_waddr, ob_raddr;
    logic [127:0]      ob_wdata, ob_rdata;
    // Control FSM
    estate_t          state;
    logic             started, s_fire, compute_done, timeout;
    logic             pk_start, pk_done, with_output, resp_done;
    logic [63:0]      cyc;
    // Simulation-visible accounting; sum equals response preparation clocks.
    logic [63:0] network_compute_cycles /* verilator public_flat_rd */;
    logic [63:0] network_weight_cycles /* verilator public_flat_rd */;
    logic [63:0] network_transition_cycles /* verilator public_flat_rd */;
    logic [31:0]      stall_cyc, wd_cnt, watchdog;
    logic [3:0]       final_status;
    logic [15:0][31:0] header;

    always_ff @(posedge aclk) begin
        rst <= !aresetn || soft_reset;
        dp_rst <= !aresetn || soft_reset || (state == E_RESP && pk_done);
    end

    assign s_fire = s_axis_tvalid && s_axis_tready;
    assign m_axis_tkeep = 16'hffff;
    assign compute_done = started && (cfg.add ? add_done : cfg.dw ? (dw_done && !dw_busy) : (ctrl_finished && !ctrl_busy)) && !arr_busy
                       && !drain_busy && !rq_busy;

    // Final status of a parsed command (parser errors take precedence).
    always_comb begin
        if (p_status != ST_OK) final_status = p_status;
        else if (!cfg.run) final_status = ST_OK;
        else if (collision) final_status = ST_INTERNAL;
        else if (param_error) final_status = ST_DESC;
        else if (overflow || array_overflow || dw_overflow) final_status = ST_OVERFLOW;
        else final_status = ST_OK;
    end

    always_ff @(posedge aclk) begin
        ctrl_start <= 1'b0;
        pk_start <= 1'b0;
        resp_done <= 1'b0;
        if (rst) begin
            state <= E_IDLE;
            started <= 1'b0;
            timeout <= 1'b0;
            cyc <= '0;
        end else begin
            case (state)
                E_IDLE: begin
                    total_issues <= 0;
                    network_compute_cycles<=0; network_weight_cycles<=0;
                    network_transition_cycles<=64'(s_fire);
                    started <= 1'b0;
                    timeout <= 1'b0;
                    stall_cyc <= '0;
                    wd_cnt <= '0;
                    cyc <= 64'(s_fire);
                    if (s_fire) state <= E_RUN;
                end
                E_RUN: begin
                    cyc <= cyc + 64'd1;
                    if(started && !compute_done) begin
                        if(!cfg.add && (cfg.dw ? dw_weight_wait : pw_weight_wait)) network_weight_cycles<=network_weight_cycles+1;
                        else network_compute_cycles<=network_compute_cycles+1;
                    end else network_transition_cycles<=network_transition_cycles+1;
                    // Input starvation: engine ready, stream idle. The
                    // watchdog pauses while the engine applies backpressure.
                    if (s_fire) wd_cnt <= '0;
                    else if (s_axis_tready) begin
                        wd_cnt <= wd_cnt + 32'd1;
                        stall_cyc <= stall_cyc + 32'd1;
                    end
                    timeout <= watchdog != 0 && wd_cnt >= watchdog;
                    if (!started && cfg.run && params_done && (unpack_done || (network_mode && layer_ready)) && p_status == ST_OK) begin
                        ctrl_start <= 1'b1;
                        started <= 1'b1;
                    end
                    if(layer_reset) begin started<=0; total_issues<=total_issues+issue_cycles; end
                    if (p_done && (p_status != ST_OK || !cfg.run || compute_done)) begin
                        state <= E_RESP;
                        pk_start <= 1'b1;
                        with_output <= final_status == ST_OK && cfg.run;
                        header[0] <= RSP_MAGIC;
                        header[1] <= PROTO_VERSION;
                        header[2] <= {28'd0, final_status};
                        header[3] <= p_tag;
                        header[4] <= cyc[31:0] + 32'd1;
                        header[5] <= cyc[63:32] + 32'(&cyc[31:0]);
                        header[6] <= stall_cyc;
                        header[7] <= total_issues+issue_cycles;
                        if (final_status == ST_OK && cfg.run) begin
                            header[8] <= 32'(cfg.co) * 32'(out_cfg.t);
                            header[9] <= 32'(cfg.co);
                            header[10] <= 32'(out_cfg.t);
                            header[11] <= 32'(out_cfg.valid);
                            header[12] <= {31'd0, !cfg.relu};
                        end else begin
                            for (int i = 8; i <= 12; i++) header[i] <= '0;
                        end
                        header[13] <= p_opcode;
                        header[14] <= p_err_beat;
                        header[15] <= ENGINE_VERSION;
                    end
                end
                E_RESP: begin
                    cyc <= cyc + 64'd1;
                    if (pk_done) begin
                        state <= E_CLEAN;
                        resp_done <= 1'b1;
                    end
                end
                default: state <= E_IDLE;   // E_CLEAN: datapath reset cycle
            endcase
        end
    end

    stt_axil_regs regs (
        .clk(aclk), .rst(!aresetn),
        .s_awaddr(s_axil_awaddr), .s_awvalid(s_axil_awvalid), .s_awready(s_axil_awready),
        .s_wdata(s_axil_wdata), .s_wstrb(s_axil_wstrb), .s_wvalid(s_axil_wvalid),
        .s_wready(s_axil_wready), .s_bresp(s_axil_bresp), .s_bvalid(s_axil_bvalid),
        .s_bready(s_axil_bready), .s_araddr(s_axil_araddr), .s_arvalid(s_axil_arvalid),
        .s_arready(s_axil_arready), .s_rdata(s_axil_rdata), .s_rresp(s_axil_rresp),
        .s_rvalid(s_axil_rvalid), .s_rready(s_axil_rready),
        .soft_reset, .watchdog, .irq,
        .busy(state != E_IDLE), .responding(state == E_RESP),
        .resp_done, .resp_status(header[2][3:0]), .resp_tag(header[3]),
        .resp_cycles(cyc), .resp_active(header[7]), .resp_stall(header[6])
    );

    stt_cmd_parser parser (
        .clk(aclk), .rst(dp_rst),
        .s_tdata(s_axis_tdata), .s_tkeep(s_axis_tkeep), .s_tvalid(s_axis_tvalid),
        .s_tlast(s_axis_tlast), .s_tready(s_axis_tready), .timeout,
        .layer_done(compute_done), .layer_status(final_status), .network_mode, .layer_reset, .layer_ready,
        .source_bank, .branch_bank, .dest_bank,
        .unpack_start, .in_valid, .in_ready, .unpack_done, .branch, .add_p_we,
        .p_we(pr_we), .p_sel(pr_sel), .p_addr(pr_addr),
        .w_valid, .w_ready,
        .cfg, .opcode(p_opcode), .tag(p_tag), .params_done,
.done(p_done), .status(p_status), .err_beat(p_err_beat)
    );

    stt_act_unpack unpack (
        .clk(aclk), .rst(dp_rst), .start(unpack_start), .cfg(unpack_cfg),
        .in_data(s_axis_tdata), .in_valid, .in_ready,
        .we(ib_we), .waddr(ib_waddr), .wdata(ib_wdata), .done(unpack_done)
    );

    stt_param_ram params (
        .clk(aclk), .we(pr_we), .sel(pr_sel), .waddr(pr_addr), .wdata(s_axis_tdata),
        .re(pr_re), .ch(pr_ch), .bias(unused_pr_bias), .mult(pr_mult), .shift(pr_shift)
    );

    stt_weight_buf #(.LAT(ACT_LAT)) weights (
        .clk(aclk), .rst(layer_rst), .kpt(cfg.kpt),
        .wr_data(s_axis_tdata), .wr_valid(w_valid), .wr_ready(w_ready),
        .rd_en(w_re), .rd_half(w_half), .rd_k(w_k), .rd_data(w_rdata),
        .full(w_full), .release_half(w_release)
    );

    stt_pw_ctrl ctrl (
        .clk(aclk), .rst(layer_rst), .start(ctrl_start && !cfg.dw && !cfg.add), .cfg,
        .w_full, .w_release(pw_rel), .w_re(pw_wr), .w_half(pw_wh), .w_k(pw_k), .w_rdata,
        .act_re(pw_ar), .act_raddr(pw_addr), .act_rdata(ib_rdata),
        .a_valid, .a_first, .a_last, .a_tag, .a_weight, .a_act,
        .finished(ctrl_finished), .busy(ctrl_busy), .issue_cycles(pw_cycles), .weight_wait(pw_weight_wait)
    );

    stt_mac_array #(.OUTPUTS(ARRAY_O), .FRAMES(ARRAY_F), .SUM_W(SUM_W), .TAG_W(TAG_W)) array (
        .clk(aclk), .rst(layer_rst),
        .in_valid(a_valid), .in_first(a_first), .in_last(a_last), .in_tag(a_tag),
        .weight(a_weight), .activation(a_act),
        .bias(tile_bias), .overflow(array_overflow),
        .out_valid(cap_valid), .out_cap(cap_cell), .out_tag(cap_tag), .sum(sums), .busy(arr_busy)
    );

    stt_acc_drain #(.OUTPUTS(ARRAY_O), .FRAMES(ARRAY_F), .SUM_W(SUM_W), .TAG_W(TAG_W)) drain (
        .clk(aclk), .rst(layer_rst),
        .cap_valid, .cap_cell, .cap_tag, .cap_sum(sums),
        .row_valid(pw_rv), .row_idx(pw_ri), .row_tag(pw_tag), .row_sum(pw_sum),
        .busy(drain_busy), .collision
    );

    stt_requant_row rows (
        .clk(aclk), .rst(layer_rst), .cfg(out_cfg),
        .row_valid, .row_idx, .row_tag, .row_sum,
        .p_re(pr_re), .p_ch(pr_ch), .p_mult(pr_mult), .p_shift(pr_shift),
        .we(rq_we), .waddr(rq_addr), .wdata(rq_data),
        .overflow, .param_error, .busy(rq_busy)
    );

    stt_act_pool pool (
        .clk(aclk),.network_mode,.source_bank,.dest_bank,
        .second_bank(cfg.add && state==E_RUN ? branch_bank : dest_bank),
        .a_we(ib_we && !(cfg.add && branch)),.b_we(ob_we),
        .a_wa(ib_waddr),.b_wa(ob_waddr),.a_wd(ib_wdata),.b_wd(ob_wdata),
        .a_re(ib_re),.a_ra(ib_raddr),.a_rd(ib_rdata),
        .b_re(cfg.add && state==E_RUN ? add_re : ob_re),
        .b_ra(cfg.add && state==E_RUN ? add_addr : ob_raddr),.b_rd(ob_rdata));

    stt_resp_packer packer (
        .clk(aclk), .rst(dp_rst), .start(pk_start), .with_output, .cfg(out_cfg), .header,
        .re(ob_re), .raddr(ob_raddr), .rdata(ob_rdata),
        .m_tdata(m_axis_tdata), .m_tvalid(m_axis_tvalid), .m_tready(m_axis_tready),
        .m_tlast(m_axis_tlast), .done(pk_done)
    );

    // Bias banks supply all 32 initial DSP feedback operands in parallel.
    for (genvar o = 0; o < ARRAY_O; o++) begin : g_bias
        (* ram_style = "distributed" *) logic [31:0] mem [32];
        always_ff @(posedge aclk)
            if (pr_we && pr_sel == 0 && pr_addr[2:0] == 3'(o/4))
                mem[pr_addr[7:3]] <= s_axis_tdata[32*(o%4) +: 32];
        assign tile_bias[o] = {1'b0, (cfg.dw ? dw_tag.otile : a_tag.otile), 5'(o)} < cfg.co ? mem[cfg.dw ? dw_tag.otile : a_tag.otile] : 32'd0;
    end
endmodule






