// QuartzNet INT8 engine that takes one command (header, input, program) and returns the logits
module stt_engine
    import stt_engine_pkg::*;
(
    input logic aclk, aresetn,
    input logic [127:0] s_axis_tdata,
    input logic s_axis_tvalid, s_axis_tlast,
    output logic s_axis_tready,
    output logic [127:0] m_axis_tdata,
    output logic m_axis_tvalid, m_axis_tlast,
    input logic m_axis_tready
);
    state_t state;
    layer_t cfg;
    logic [16:0] count;
    logic [1:0] slot;
    logic accept, rst, start, done;

    logic [3:0][31:0] params;
    logic [9:0] param_ch;
    logic [ROWS - 1:0][31:0] bias;
    logic [4:0] bias_otile;

    logic pool_we, adding, w_ready, w_half;
    logic [1:0] pool_bank, b_bank;
    logic [AW - 1:0] pool_address, a_address, b_address;
    logic [127:0] pool_data, a_data, b_data;
    logic [1:0] w_full, w_release;
    logic [9:0] w_k;
    logic [255:0] w_data;

    logic pw_half, pw_done, a_valid, a_first, a_last, array_valid;
    logic [1:0] pw_release;
    logic [9:0] pw_k;
    logic [AW - 1:0] pw_address;
    tile_tag_t a_tag, array_tag;
    logic [ROWS - 1:0][7:0] a_weight;
    logic [FRAMES - 1:0][8:0] a_act;
    logic [4:0] array_index;
    logic [FRAMES - 1:0][31:0] array_sum;
    logic dw_half, dw_done, dw_valid;
    logic [1:0] dw_release;
    logic [9:0] dw_k;
    logic [AW - 1:0] dw_address;
    tile_tag_t dw_tag;
    logic [4:0] dw_index;
    logic [FRAMES - 1:0][31:0] dw_sum;
    logic add_we, add_done;
    logic [AW - 1:0] add_address;
    logic [9:0] add_ch;
    logic [127:0] add_data;
    logic rq_we;
    logic [9:0] rq_ch;
    logic [AW - 1:0] rq_address;
    logic [127:0] rq_data;

    assign accept = s_axis_tvalid && s_axis_tready;
    assign done = cfg.add ? add_done : cfg.dw ? dw_done : pw_done;
    stt_sequencer sequencer(
        aclk, aresetn, s_axis_tdata, s_axis_tvalid, s_axis_tlast, s_axis_tready, m_axis_tdata, m_axis_tvalid,
        m_axis_tlast, m_axis_tready, b_data, done, w_ready, state, cfg, count, slot, rst, start);

    assign param_ch = cfg.add ? add_ch : rq_ch;
    assign bias_otile = cfg.dw ? dw_tag.otile : a_tag.otile;
    stt_layer_params parameters(
        aclk, state == PARAMS && accept, slot, 8'(count), s_axis_tdata, param_ch, params, bias_otile, bias);

    // Bank 0 takes the input, and port B reads an add's second input, then the reply
    assign pool_we = state == INPUT ? accept : cfg.add ? add_we : rq_we;
    assign pool_bank = state == INPUT ? 2'd0 : cfg.dest;
    assign pool_address = state == INPUT ? AW'(count) : cfg.add ? add_address : rq_address;
    assign pool_data = state == INPUT ? s_axis_tdata : cfg.add ? add_data : rq_data;
    assign a_address = cfg.add ? add_address : cfg.dw ? dw_address : pw_address;
    assign adding = cfg.add && state == FINISH;
    assign b_bank = adding ? cfg.branch : cfg.dest;
    assign b_address = adding ? add_address : AW'(count);
    stt_act_banks banks(
        aclk, pool_we, pool_bank, pool_address, pool_data, cfg.source, b_bank, a_address, b_address, a_data, b_data);

    assign w_half = cfg.dw ? dw_half : pw_half;
    assign w_k = cfg.dw ? dw_k : pw_k;
    assign w_release = cfg.dw ? dw_release : pw_release;
    stt_weight_buf weights(
        aclk, rst, cfg.kpt, s_axis_tdata, state == WEIGHTS && s_axis_tvalid, w_ready, w_half, w_k, w_data, w_full,
        w_release);

    stt_pw_ctrl pw(
        aclk, rst, start && !cfg.dw && !cfg.add, cfg.ci, cfg.nt, cfg.n_otile, cfg.valid, cfg.in_signed, w_full,
        pw_release, pw_half, pw_k, w_data, pw_address, a_data, a_valid, a_first, a_last, a_tag, a_weight, a_act,
        pw_done);
    stt_pw_array array(
        aclk, rst, a_valid, a_first, a_last, a_tag, a_weight, a_act, bias, array_valid, array_index, array_tag,
        array_sum);
    stt_dw_ctrl dw(
        aclk, rst, start && cfg.dw, cfg.nt, cfg.nt_out, cfg.co, cfg.taps, cfg.padding, cfg.stride, cfg.dilation,
        cfg.valid, cfg.in_signed, w_full, dw_release, dw_half, dw_k, w_data, dw_address, a_data, bias, dw_valid,
        dw_index, dw_tag, dw_sum, dw_done);
    stt_requant requant(
        aclk, rst, cfg.co, cfg.nt_out, cfg.relu, cfg.dw ? dw_valid : array_valid, cfg.dw ? dw_index : array_index,
        cfg.dw ? dw_tag : array_tag, cfg.dw ? dw_sum : array_sum, rq_ch, params[1], params[2], rq_we, rq_address,
        rq_data);
    stt_add_ctrl add(
        aclk, rst, start && cfg.add, cfg.co, cfg.nt, add_address, add_ch, a_data, b_data, params, add_we, add_data,
        add_done);
endmodule
