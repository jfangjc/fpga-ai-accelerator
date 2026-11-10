// QNC2 command parser (stage S1: PING and pointwise RUN_LAYER).
//
// Consumes the command stream beat by beat in the order
//   header (4 beats) | input [Ci][T] bytes | QLR2 record header (4 beats) |
//   bias | multiplier | shift | weight tiles,
// validates the header, record and requantization parameters, and routes
// payload beats (s_tdata) to the activation unpacker, the parameter RAM and
// the weight ping-pong buffer. The first error is latched with the number
// of beats accepted so far; the parser then drains through TLAST (unless
// the offending beat carried it) and reports done. Magic words inside a
// payload are never interpreted: resynchronisation happens only at TLAST.
module stt_cmd_parser
    import stt_engine_pkg::*;
(
    input  logic         clk,
    input  logic         rst,
    // Command stream (AXI4-Stream slave)
    input  logic [127:0] s_tdata,
    input  logic [15:0]  s_tkeep,
    input  logic         s_tvalid,
    input  logic         s_tlast,
    output logic         s_tready,
    input logic layer_done,
    input logic [3:0] layer_status,
    output logic network_mode, layer_reset, layer_ready,
    output logic [2:0] source_bank, branch_bank, dest_bank,
    input  logic         timeout,       // idle watchdog: abort the command
    // Activation unpacker
    output logic         unpack_start,
    output logic         in_valid,
    input  logic         in_ready,
    input logic unpack_done,
    output logic branch,
    output logic add_p_we,
    // Parameter RAM write port (data is s_tdata)
    output logic         p_we,
    output logic [1:0]   p_sel,
    output logic [7:0]   p_addr,
    // Weight buffer write port (data is s_tdata)
    output logic         w_valid,
    input  logic         w_ready,
    // Decoded command
    output layer_cfg_t   cfg,
    output logic [31:0]  opcode,        // zero until the header is complete
    output logic [31:0]  tag,
    output logic         params_done,
    output logic         done,          // consumed through TLAST, or aborted
    output logic [3:0]   status,
    output logic [31:0]  err_beat       // beats accepted when the error latched
);
    typedef enum logic [4:0] {
        P_HDR, P_CHECK, P_INPUT, P_REC, P_RCHECK, P_BIAS, P_MULT, P_SHIFT,
        P_WEIGHT, P_DRAIN, P_DONE, P_WAIT, P_ADD, P_NET_HEADER, P_NET_REC, P_NET_SETUP, P_NET_WAIT, P_NET_RESET, P_GEOM
    } phase_t;

    phase_t      phase;
    logic [7:0] record_index;
    logic [31:0] network_bytes;
    logic [9:0] bank_t [5], bank_valid [5];
    logic bank_signed [5];
    logic [511:0] expected_record;
    logic [8:0] routing;
    assign expected_record=network_record(record_index);
    assign routing=network_banks(record_index);
    assign source_bank=network_mode ? routing[2:0] : 3'd0;
    assign branch_bank=network_mode ? routing[5:3] : 3'd1;
    assign dest_bank=network_mode ? routing[8:6] : 3'd1;
    assign layer_ready=network_mode && record_index!=0;

    logic [16:0] cnt;           // beat index within the phase
    logic [31:0] beats;         // beats accepted in this command
    logic [31:0] hw [16];       // command header words
    logic [31:0] rw [16];       // QLR2 record header words
    logic        hdr_complete;
    logic        busy;          // first beat accepted
    logic        last3;         // TLAST on header beat 3
    logic [2:0]  step;          // header check sequence
    logic        e_magic, e_op, e_desc, is_run;
    logic [20:0] prod_ct;       // ci*t input bytes
    logic [11:0] prod_w;        // n_otile*kpt weight K tiles
    logic [16:0] nin;           // input beats
    logic [16:0] wbeats;        // weight beats
    logic [8:0]  nb;            // beats per parameter array
    logic [31:0] rec_bytes, payload;
    logic        fire, keep_bad, param_bad;
    logic [31:0] beats_n;
    logic [31:0] lane [4];

    // Geometry is only consumed after the full-width scalar limits pass.
    // Legal dilation is 1/2: use a shift, never a general DSP multiply.
    // Four startup clocks separate tap span, padded lengths, subtraction and
    // floor division. Signed intermediates preserve valid=0 with padding.
    logic [7:0] geom_span, geom_pad;
    logic signed [12:0] padded_t, padded_v, numerator_t, numerator_v;
    logic signed [12:0] geom_t, geom_v;
    assign fire = s_tvalid && s_tready;
    assign beats_n = beats + 32'(fire);
    assign keep_bad = s_tkeep != 16'hffff;
    for (genvar i = 0; i < 4; i++) begin : g_lane
        assign lane[i] = s_tdata[32*i +: 32];
    end

    always_comb begin
        case (phase)
            P_NET_HEADER, P_NET_REC, P_HDR, P_REC, P_BIAS, P_MULT, P_SHIFT, P_ADD, P_DRAIN: s_tready = 1'b1;
            P_INPUT:  s_tready = in_ready;
            P_WEIGHT: s_tready = w_ready;
            default:  s_tready = 1'b0;
        endcase
    end
    assign in_valid = phase == P_INPUT && s_tvalid;
    assign w_valid = phase == P_WEIGHT && s_tvalid;
    assign p_we = fire && (phase == P_BIAS || phase == P_MULT || phase == P_SHIFT);
    assign p_sel = phase == P_ADD ? step[1:0] : phase == P_BIAS ? 2'd0 : (phase == P_MULT ? 2'd1 : 2'd2);
    assign p_addr = cnt[7:0];
    assign add_p_we = fire && phase == P_ADD;
    assign done = phase == P_DONE;
    assign opcode = hdr_complete ? hw[2] : 32'd0;
    assign tag = hdr_complete ? hw[3] : 32'd0;

    // Real channels need a positive multiplier and 31+shift >= 1 (the
    // golden model rejects left shifts). Padding lanes >= C_out are ignored.
    always_comb begin
        param_bad = 1'b0;
        for (int i = 0; i < 4; i++) begin
            if ({cnt[8:0], 2'(i)} < cfg.co) begin
                if ((phase == P_MULT || (phase == P_ADD && !step[0])) && $signed(lane[i]) <= 0) param_bad = 1'b1;
                if ((phase == P_SHIFT || (phase == P_ADD && step[0])) && $signed(lane[i]) < -32'sd30) param_bad = 1'b1;
            end
        end
    end

    // Latch the first error; continue to P_DONE if TLAST was seen, else drain.
    task automatic fail(input logic [3:0] code, input logic seen_last);
        if (status == ST_OK) begin
            status <= code;
            err_beat <= beats_n;
        end
        phase <= seen_last ? P_DONE : P_DRAIN;
    endtask

    always_ff @(posedge clk) begin
        unpack_start <= 1'b0;
        layer_reset <= 0;
        if (rst) begin
            phase <= P_HDR;
            network_mode <= 0; record_index <= 0;
            branch <= 0;
            cnt <= '0;
            beats <= '0;
            status <= ST_OK;
            err_beat <= '0;
            busy <= 1'b0;
            hdr_complete <= 1'b0;
            params_done <= 1'b0;
            step <= '0;
        end else begin
            if (fire) begin
                beats <= beats_n;
                busy <= 1'b1;
            end
            if (timeout && busy && phase != P_DONE) begin
                fail(ST_TIMEOUT, 1'b1);
            end else begin
                case (phase)
                    P_HDR: if (fire) begin
                        for (int i = 0; i < 4; i++) hw[{cnt[1:0], 2'(i)}] <= lane[i];
                        if (keep_bad) fail(ST_LENGTH, s_tlast);
                        else if (cnt[1:0] == 2'd3) begin
                            hdr_complete <= 1'b1;
                            last3 <= s_tlast;
                            phase <= P_GEOM;
                            step <= '0;
                            cnt <= '0;
                        end else if (s_tlast) fail(ST_LENGTH, 1'b1);
                        else cnt <= cnt + 17'd1;
                    end
                    P_GEOM: begin
                        step <= step + 3'd1;
                        case (step)
                            0: begin
                                geom_span <= (8'(hw[11][6:0])-8'd1) << (hw[13] == 2 ? 1 : 0);
                                geom_pad <= {hw[14][6:0], 1'b0};
                            end
                            1: begin
                                padded_t <= $signed({3'd0, hw[6][9:0]}) + $signed({5'd0, geom_pad}) - 13'sd1;
                                padded_v <= $signed({3'd0, hw[7][9:0]}) + $signed({5'd0, geom_pad}) - 13'sd1;
                            end
                            2: begin
                                numerator_t <= padded_t - $signed({5'd0, geom_span});
                                numerator_v <= padded_v - $signed({5'd0, geom_span});
                            end
                            default: begin
                                geom_t <= (hw[12] == 2 ? (numerator_t >>> 1) : numerator_t) + 13'sd1;
                                geom_v <= (hw[12] == 2 ? (numerator_v >>> 1) : numerator_v) + 13'sd1;
                                phase <= P_CHECK;
                                step <= 0;
                            end
                        endcase
                    end
                    P_CHECK: begin
                        step <= step + 3'd1;
                        case (step)
                            3'd0: begin
                                e_magic <= hw[0] != CMD_MAGIC || hw[1] != PROTO_VERSION;
                                e_op <= hw[2] != OP_PING && hw[2] != OP_RUN_LAYER && hw[2] != 2;
                                is_run <= hw[2] == OP_RUN_LAYER || network_mode;
                                e_desc <= hw[5] == 0 || hw[5] > MAX_CI || hw[6] == 0
                                    || hw[6] > MAX_T || hw[7] > hw[6] || hw[8] == 0
                                    || hw[8] > MAX_CO || hw[9][31:1] != 0
                                    || hw[15] != 0 || hw[10] > 2
                                    || (hw[10] == 2 && (hw[5] != hw[8] || hw[9] != 1 || (hw[11]|hw[12]|hw[13]|hw[14]) != 0))
                                    || (hw[10] == 0 && (hw[11]|hw[12]|hw[13]|hw[14]) != 0)
                                    || (hw[10] == 1 && (hw[5] != hw[8] || hw[11] < 1 || hw[11] > 87
                                        || hw[12] < 1 || hw[12] > 2 || hw[13] < 1 || hw[13] > 2 || hw[14] > 86
                                        || geom_t < 1 || geom_t > 800));
                                cfg.dw <= hw[10] == 1;
                                cfg.add <= hw[10] == 2;
                                cfg.taps <= 7'(hw[11]); cfg.stride <= 2'(hw[12]);
                                cfg.dilation <= 2'(hw[13]); cfg.padding <= 7'(hw[14]);
                                cfg.tout <= hw[10] == 1 ? 10'(geom_t) : 10'(hw[6]);
                                cfg.vout <= hw[10] == 1 ? 10'(geom_v > 0 ? geom_v : 13'sd0) : 10'(hw[7]);
                                cfg.run <= hw[2] == OP_RUN_LAYER || network_mode;
                                cfg.in_signed <= hw[9][0];
                                cfg.relu <= 1'b0;
                                cfg.ci <= hw[5][10:0];
                                cfg.t <= hw[6][9:0];
                                cfg.valid <= hw[7][9:0];
                                cfg.co <= hw[8][10:0];
                            end
                            3'd1: begin
                                cfg.nt <= 6'((cfg.t + 10'd15) >> 4);
                                cfg.last_n <= {1'b0, 4'(cfg.t - 10'd1)} + 5'd1;
                                cfg.n_otile <= 6'((cfg.co + 11'd31) >> 5);
                                cfg.kpt <= cfg.dw ? 7'((32'(cfg.taps)+15)/16) : 7'((cfg.ci + 11'd15) >> 4);
                                nb <= 9'((cfg.co + 11'd3) >> 2);
                                prod_ct <= 21'(cfg.ci) * 21'(cfg.t);
                            end
                            3'd2: begin
                                prod_w <= 12'(cfg.n_otile) * 12'(cfg.kpt);
                                nin <= 17'((prod_ct + 21'd15) >> 4);
                            end
                            3'd3: begin
                                wbeats <= 17'(prod_w) << 5;
                                rec_bytes <= cfg.add ? 32'd64 + 32'd64 * 32'(nb) : 32'd64 + 32'd48 * 32'(nb) + (32'(prod_w) << 9);
                            end
                            3'd4: payload <= (32'(nin) << (cfg.add ? 5 : 4)) + rec_bytes;
                            default: begin
                                if (hw[2]==2 && !network_mode && !e_magic) begin
                                    if(hw[5]!=64 || hw[6]<1 || hw[6]>640 || hw[7]>hw[6] || hw[8]!=29 || hw[9]!=1 || hw[10]!=186 || hw[11]!=20037392 || (hw[12]|hw[13]|hw[14]|hw[15])!=0) fail(ST_DESC,last3);
                                    else if(last3 || hw[4]!=64*hw[6]+hw[11]) fail(ST_LENGTH,last3);
                                    else begin
                                        network_mode<=1; network_bytes<=hw[11];
                                        bank_t[0]<=10'(hw[6]); bank_valid[0]<=10'(hw[7]); bank_signed[0]<=1;
                                        cfg.add<=0; cfg.dw<=0;
                                        phase<=P_INPUT; unpack_start<=1;
                                    end
                                end else if (e_magic) fail(ST_MAGIC, last3);
                                else if (e_op) fail(ST_OPCODE, last3);
                                else if (!is_run) begin
                                    if (hw[4] != 0 || !last3) fail(ST_LENGTH, last3);
                                    else phase <= P_DONE;
                                end
                                else if (e_desc) fail(ST_DESC, last3);
                                else if ((!network_mode && hw[4] != payload) || last3) fail(ST_LENGTH, last3);
                                else begin
                                    phase <= network_mode ? P_RCHECK : P_INPUT;
                                    unpack_start <= !network_mode;
                                end
                            end
                        endcase
                    end
                    P_INPUT: if (fire) begin
                        if (keep_bad || s_tlast) fail(ST_LENGTH, s_tlast);
                        else if (cnt == nin - 17'd1) begin
                            phase <= network_mode ? P_NET_HEADER : cfg.add ? P_WAIT : P_REC;
                            cnt <= '0;
                        end else cnt <= cnt + 17'd1;
                    end
                    P_WAIT: if (unpack_done) begin
                        if (!branch) begin
                            branch <= 1;
                            unpack_start <= 1;
                            phase <= P_INPUT;
                        end else phase <= P_REC;
                    end
                    P_ADD: if (fire) begin
                        if (keep_bad) fail(ST_LENGTH, s_tlast);
                        else if (param_bad) fail(ST_DESC, s_tlast);
                        else if (cnt == 17'(nb)-1) begin
                            cnt <= 0;
                            if (step == 3) begin
                                if (s_tlast != (!network_mode || record_index==186)) fail(ST_LENGTH,s_tlast);
                                else begin params_done <= 1; phase <= network_mode ? P_NET_WAIT : P_DONE; end
                            end else if (s_tlast) fail(ST_LENGTH, 1);
                            else step <= step+1;
                        end else if (s_tlast) fail(ST_LENGTH, 1);
                        else cnt <= cnt+1;
                    end
                    P_REC: if (fire) begin
                        for (int i = 0; i < 4; i++) rw[{cnt[1:0], 2'(i)}] <= lane[i];
                        if (keep_bad || s_tlast) fail(ST_LENGTH, s_tlast);
                        else if (cnt[1:0] == 2'd3) begin
                            phase <= P_RCHECK;
                            cnt <= '0;
                        end else cnt <= cnt + 17'd1;
                    end
                    P_RCHECK: begin
                        // Words 2, 4, 5 (tensor ids) are informational only.
                        if (cfg.add) begin
                            if (rw[0] != REC_MAGIC || rw[1] != rec_bytes || rw[3] != 2
                                || rw[6] != 1 || rw[7] != hw[8] || (rw[8]|rw[9]|rw[10]|rw[11]|rw[12]|rw[13]|rw[14]|rw[15]) != 0)
                                fail(ST_DESC, 0);
                            else begin cfg.relu <= 1; phase <= P_ADD; step <= 0; end
                        end else if (rw[0] != REC_MAGIC || rw[1] != rec_bytes || rw[3] != 32'd1
                            || rw[6] > 32'd1 || rw[7] != hw[8] || rw[8] != (cfg.dw ? 32'd1 : hw[5])
                            || rw[9] != (cfg.dw ? hw[11] : 32'd1) || rw[10] != (cfg.dw ? hw[12] : 32'd1) || rw[11] != (cfg.dw ? hw[13] : 32'd1)
                            || rw[12] != (cfg.dw ? hw[14] : 32'd0) || rw[13] != (cfg.dw ? hw[8] : 32'd1) || (rw[14] | rw[15]) != 0)
                            fail(ST_DESC, 1'b0);
                        else begin
                            cfg.relu <= rw[6][0];
                            phase <= P_BIAS;
                        end
                    end
                    P_BIAS, P_MULT, P_SHIFT: if (fire) begin
                        if (keep_bad) fail(ST_LENGTH, s_tlast);
                        else if (param_bad) fail(ST_DESC, s_tlast);
                        else if (s_tlast) fail(ST_LENGTH, 1'b1);
                        else if (cnt == 17'(nb) - 17'd1) begin
                            cnt <= '0;
                            if (phase == P_BIAS) phase <= P_MULT;
                            else if (phase == P_MULT) phase <= P_SHIFT;
                            else begin
                                phase <= P_WEIGHT;
                                params_done <= 1'b1;
                            end
                        end else cnt <= cnt + 17'd1;
                    end
                    P_WEIGHT: if (fire) begin
                        if (keep_bad) fail(ST_LENGTH, s_tlast);
                        else if (cnt == wbeats - 17'd1) begin
                            if(s_tlast != (!network_mode || record_index==186)) fail(ST_LENGTH,s_tlast);
                            else phase<=network_mode ? P_NET_WAIT : P_DONE;
                        end else if (s_tlast) fail(ST_LENGTH, 1'b1);
                        else cnt <= cnt + 17'd1;
                    end
                    P_NET_HEADER: if(fire) begin
                        if(keep_bad || s_tlast) fail(ST_LENGTH,s_tlast);
                        else if(lane[0]!=32'h32504e51 || lane[1]!=1 || lane[2]!=186 || lane[3]!=network_bytes) fail(ST_DESC,0);
                        else begin record_index<=1; cnt<=0; phase<=P_NET_REC; end
                    end
                    P_NET_REC: if(fire) begin
                        for(int i=0;i<4;i++) rw[{cnt[1:0],2'(i)}]<=lane[i];
                        if(keep_bad || s_tlast) fail(ST_LENGTH,s_tlast);
                        else if(s_tdata!=expected_record[128*cnt[1:0]+:128]) fail(ST_DESC,0);
                        else if(cnt==3) begin cnt<=0; phase<=P_NET_SETUP; end
                        else cnt<=cnt+1;
                    end
                    P_NET_SETUP: begin
                        hw[5]<=rw[3]==2 || rw[13]!=1 ? rw[7] : rw[8];
                        hw[6]<=32'(bank_t[source_bank]); hw[7]<=32'(bank_valid[source_bank]);
                        hw[8]<=rw[7]; hw[9]<=32'(bank_signed[source_bank]);
                        hw[10]<=rw[3]==2 ? 2 : rw[13]!=1 ? 1 : 0;
                        hw[11]<=rw[13]!=1 ? rw[9] : 0; hw[12]<=rw[13]!=1 ? rw[10] : 0;
                        hw[13]<=rw[13]!=1 ? rw[11] : 0; hw[14]<=rw[13]!=1 ? rw[12] : 0;
                        last3<=0; step<=0; phase<=P_GEOM;
                    end
                    P_NET_WAIT: if(layer_done) begin
                        if(layer_status!=ST_OK) fail(layer_status,record_index==186);
                        else begin
                        bank_t[dest_bank]<=cfg.tout; bank_valid[dest_bank]<=cfg.vout; bank_signed[dest_bank]<=!cfg.relu;
                        if(record_index==186) phase<=P_DONE;
                        else begin layer_reset<=1; params_done<=0; phase<=P_NET_RESET; end
                    end
                        end
                    P_NET_RESET: begin cnt<=0; record_index<=record_index+1; phase<=P_NET_REC; end
                    P_DRAIN: if (fire && s_tlast) phase <= P_DONE;
                    default: ;
                endcase
            end
        end
    end
endmodule






