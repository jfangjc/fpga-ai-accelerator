// Engine sequencer that reads the command, runs one layer per record and sends the reply
// The command header is channels, frames and valid frames, the reply header frames and valid frames
module stt_sequencer
    import stt_engine_pkg::*;
(
    input logic aclk, aresetn,
    input logic [127:0] s_axis_tdata,
    input logic s_axis_tvalid, s_axis_tlast,
    output logic s_axis_tready,
    output logic [127:0] m_axis_tdata,
    output logic m_axis_tvalid, m_axis_tlast,
    input logic m_axis_tready,
    input logic [127:0] reply_data,
    input logic done, w_ready,
    output state_t state,
    output layer_t cfg,
    output logic [16:0] count,
    output logic [1:0] slot,
    output logic rst, start
);
    record_t record;
    logic [9:0] frames[BANKS], valid[BANKS];  // per bank
    logic is_signed[BANKS];
    logic [10:0] channels;
    logic [16:0] words;
    logic [8:0] beats;
    logic [7:0] span;
    logic signed [11:0] reach_t, reach_v, out_t, out_v;
    logic [5:0] drain;
    logic [2:0] step;
    logic accept, last;

    assign record = s_axis_tdata;
    assign accept = s_axis_tvalid && s_axis_tready;
    assign out_t = (cfg.stride == 2 ? reach_t >>> 1 : reach_t) + 12'sd1;
    assign out_v = (cfg.stride == 2 ? reach_v >>> 1 : reach_v) + 12'sd1;

    always_comb begin
        case (state)
            HEADER, INPUT, RECORD, PARAMS: s_axis_tready = 1;
            WEIGHTS: s_axis_tready = w_ready;
            default: s_axis_tready = 0;
        endcase
    end

    assign m_axis_tvalid = state == REPLY || state == SEND;
    assign m_axis_tdata = state == REPLY ? {64'd0, 22'd0, cfg.vout, 22'd0, cfg.tout} : reply_data;
    assign m_axis_tlast = state == SEND && count == words - 1;

    always_ff @(posedge aclk) begin
        rst <= !aresetn || state == RECORD; // layer units reset between layers
        start <= 0;
        if (!aresetn) begin
            state <= HEADER;
        end
        else begin
            case (state)
                HEADER: if (accept) begin
                    channels <= 11'(s_axis_tdata[31:0]);
                    frames[0] <= 10'(s_axis_tdata[63:32]);
                    valid[0] <= 10'(s_axis_tdata[95:64]);
                    is_signed[0] <= 1;
                    state <= SIZE;
                end
                SIZE: begin
                    words <= channels * ((frames[0] + 15) >> 4);
                    count <= 0;
                    state <= INPUT;
                end
                INPUT: if (accept) begin
                    count <= count + 1;
                    if (count == words - 1) begin
                        state <= RECORD;
                    end
                end
                RECORD: if (accept) begin
                    cfg.dw <= record.kind == 1;
                    cfg.add <= record.kind == 2;
                    cfg.relu <= record.relu[0];
                    cfg.source <= 2'(record.source);
                    cfg.branch <= 2'(record.branch);
                    cfg.dest <= 2'(record.dest);
                    cfg.co <= 11'(record.co);
                    cfg.ci <= 11'(record.ci);
                    cfg.taps <= 7'(record.taps);
                    cfg.stride <= 2'(record.stride);
                    cfg.dilation <= 2'(record.dilation);
                    cfg.padding <= 7'(record.padding);
                    step <= 0;
                    state <= SETUP;
                end
                // Depthwise output frames = (frames + 2 * padding - dilation * (taps - 1) - 1) / stride + 1
                SETUP: begin
                    step <= step + 1;
                    case (step)
                        0: begin
                            cfg.t <= frames[cfg.source];
                            cfg.valid <= valid[cfg.source];
                            cfg.in_signed <= is_signed[cfg.source];
                            cfg.n_otile <= 6'((cfg.co + 31) >> 5);
                            cfg.kpt <= 7'(((cfg.dw ? 11'(cfg.taps) : cfg.ci) + 15) >> 4);
                            beats <= 9'((cfg.co + 3) >> 2);
                            span <= 8'(cfg.taps - 1) << (cfg.dilation == 2);
                        end
                        1: begin
                            cfg.nt <= 6'((cfg.t + 15) >> 4);
                            reach_t <= 12'(cfg.t + 2 * cfg.padding - span - 1);
                            reach_v <= 12'(cfg.valid + 2 * cfg.padding - span - 1);
                            words <= cfg.n_otile * cfg.kpt << 5;
                        end
                        2: begin
                            cfg.tout <= cfg.dw ? 10'(out_t) : cfg.t;
                            cfg.vout <= !cfg.dw ? cfg.valid : out_v > 0 ? 10'(out_v) : 0;
                        end
                        default: begin
                            cfg.nt_out <= 6'((cfg.tout + 15) >> 4);
                            count <= 0;
                            slot <= 0;
                            drain <= 0;
                            state <= PARAMS;
                        end
                    endcase
                end
                PARAMS: if (accept) begin
                    count <= count + 1;
                    if (count == beats - 1) begin
                        count <= 0;
                        slot <= slot + 1;
                        if (slot == (cfg.add ? 3 : 2)) begin
                            start <= 1;
                            last <= s_axis_tlast;
                            state <= cfg.add ? FINISH : WEIGHTS;
                        end
                    end
                end
                WEIGHTS: if (accept) begin
                    count <= count + 1;
                    if (count == words - 1) begin
                        last <= s_axis_tlast;
                        state <= FINISH;
                    end
                end
                FINISH: if (done) begin
                    drain <= drain + 1;
                    if (drain == DRAIN - 1) begin
                        frames[cfg.dest] <= cfg.tout;
                        valid[cfg.dest] <= cfg.vout;
                        is_signed[cfg.dest] <= !cfg.relu;
                        words <= cfg.co * cfg.nt_out;
                        state <= last ? REPLY : RECORD;
                    end
                end
                // Reply header, then the output words
                REPLY: if (m_axis_tready) begin
                    count <= 0;
                    step <= 0;
                    state <= WAIT;
                end
                WAIT: begin
                    step <= step + 1;
                    if (step == LAT - 1) begin
                        state <= SEND;
                    end
                end
                SEND: if (m_axis_tready) begin
                    count <= count + 1;
                    step <= 0;
                    state <= count == words - 1 ? HEADER : WAIT;
                end
                default: state <= HEADER;
            endcase
        end
    end
endmodule
