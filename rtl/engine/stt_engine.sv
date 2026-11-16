/*
    quartznet int8 engine -> one command in over axi stream and the logits back out
    -> the command is a 16 byte header (channels and frames and valid frames) then the input features then the program
    -> the program is one 16 byte record plus params plus weights per layer and all 186 layers run back to back
    -> activations never leave the pl -> each layer reads one of 3 ultraram banks and writes another
    -> weights only stream through once per run and get reused for every 16 frame tile
    -> once the last layer finishes the reply is a 16 byte header (frames and valid frames) then the logits
*/
module stt_engine
    import stt_engine_pkg::*;
(
    input logic aclk, aresetn,                   // 150 mhz and active low reset from the eng_rst gpio
    input logic [127:0] s_axis_tdata,            // from the engine dma mm2s -> header then input then program
    input logic s_axis_tvalid, s_axis_tlast,     // tlast marks the end of the last layer's program
    output logic s_axis_tready,
    output logic [127:0] m_axis_tdata,           // to the engine dma s2mm -> reply header then logits
    output logic m_axis_tvalid, m_axis_tlast,
    input logic m_axis_tready
);
    typedef enum logic [3:0] {
        HEADER, SIZE, INPUT, RECORD, SETUP, PARAMS, WEIGHTS, FINISH, REPLY, WAIT, SEND
    } state_t;
    // the layer that's running -> filled in from its record
    typedef struct packed {
        logic dw, add;           // depthwise or add -> neither means pointwise
        logic relu;              // relu so the output is uint8 instead of int8
        logic in_signed;         // the input is int8 instead of uint8
        logic [1:0] source, branch, dest;   // bank read on port a then the add's second bank then the bank written
        logic [10:0] ci, co;     // input and output channels
        logic [6:0] taps, padding;
        logic [1:0] stride, dilation;
        logic [9:0] t, valid;    // input frames and how many of them are real
        logic [9:0] tout, vout;  // output frames and how many of them are real
        logic [5:0] nt, nt_out;  // 16 frame tiles in and out
        logic [5:0] n_otile;     // 32 channel output tiles
        logic [6:0] kpt;         // weight rows per tile / 16
    } layer_t;
    state_t state;
    layer_t cfg;
    record_t record;
    logic [9:0] frames[BANKS], valid[BANKS];  // per bank -> how many frames the data in it has
    logic is_signed[BANKS];                   // per bank -> int8 or uint8
    logic [10:0] channels;
    logic [16:0] count, words;
    logic [8:0] beats;          // beats per parameter array
    logic [7:0] span;           // how far the taps reach - 1
    logic signed [11:0] reach_t, reach_v, out_t, out_v;
    logic [5:0] drain;
    logic [2:0] step;
    logic [1:0] slot;           // which parameter array is arriving
    logic accept, rst, start, last, done;

    logic [3:0][31:0] params;
    logic [9:0] param_ch;
    logic [ROWS-1:0][31:0] bias;
    logic [4:0] bias_otile;

    logic pool_we, adding, w_ready, w_half;
    logic [1:0] pool_bank, b_bank;
    logic [AW-1:0] pool_address, a_address, b_address;
    logic [127:0] pool_data, a_data, b_data;
    logic [1:0] w_full, w_release;
    logic [9:0] w_k;
    logic [255:0] w_data;

    // NOTE: wires between the units
    logic pw_half, pw_done, a_valid, a_first, a_last, array_valid;
    logic [1:0] pw_release;
    logic [9:0] pw_k;
    logic [AW-1:0] pw_address;
    tile_tag_t a_tag, array_tag;
    logic [ROWS-1:0][7:0] a_weight;
    logic [FRAMES-1:0][8:0] a_act;
    logic [4:0] array_index;
    logic [FRAMES-1:0][31:0] array_sum;
    logic dw_half, dw_done, dw_valid;
    logic [1:0] dw_release;
    logic [9:0] dw_k;
    logic [AW-1:0] dw_address;
    tile_tag_t dw_tag;
    logic [4:0] dw_index;
    logic [FRAMES-1:0][31:0] dw_sum;
    logic add_we, add_done;
    logic [AW-1:0] add_address;
    logic [9:0] add_ch;
    logic [127:0] add_data;
    logic rq_we;
    logic [9:0] rq_ch;
    logic [AW-1:0] rq_address;
    logic [127:0] rq_data;

    // a beat in the RECORD state is read as a record -> done comes from whichever unit runs this layer
    assign record = s_axis_tdata;
    assign accept = s_axis_tvalid && s_axis_tready;
    assign done = cfg.add ? add_done : cfg.dw ? dw_done : pw_done;
    // depthwise output frames -> stride 2 halves them
    assign out_t = (cfg.stride == 2 ? reach_t >>> 1 : reach_t) + 12'sd1;
    assign out_v = (cfg.stride == 2 ? reach_v >>> 1 : reach_v) + 12'sd1;

    // NOTE: axi stream handshakes

    // takes input beats in every receiving state but only takes weights while the weight buffer has room
    always_comb
        case (state)
            HEADER, INPUT, RECORD, PARAMS: s_axis_tready = 1;
            WEIGHTS: s_axis_tready = w_ready;
            default: s_axis_tready = 0;
        endcase
    // sends the reply header then the logits read from the last layer's bank on port b
    assign m_axis_tvalid = state == REPLY || state == SEND;
    assign m_axis_tdata = state == REPLY ? {64'd0, 22'd0, cfg.vout, 22'd0, cfg.tout} : b_data;
    assign m_axis_tlast = state == SEND && count == words - 1;

    // NOTE: the run state machine (header -> input -> 186 x layer -> reply)

    // walks through the command stream one part at a time
    always_ff @(posedge aclk) begin
        rst <= !aresetn || state == RECORD; // the layer units reset between layers
        start <= 0;
        if (!aresetn) begin
            state <= HEADER;
        end
        else begin
            case (state)
                // -> first beat of a run -> 64 channels and T frames and how many are real
                HEADER: if (accept) begin
                    channels <= 11'(s_axis_tdata[31:0]);
                    frames[0] <= 10'(s_axis_tdata[63:32]);
                    valid[0] <= 10'(s_axis_tdata[95:64]);
                    is_signed[0] <= 1;
                    state <= SIZE;
                end
                // -> one word per channel per 16 frames
                SIZE: begin
                    words <= channels * ((frames[0] + 15) >> 4);
                    count <= 0;
                    state <= INPUT;
                end
                // -> each feature beat goes straight into bank 0 (see the pool wiring below)
                INPUT: if (accept) begin
                    count <= count + 1;
                    if (count == words - 1) begin
                        state <= RECORD;
                    end
                end
                // -> one beat is one layer -> its kind and banks and shape
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
                // -> a few cycles of maths for the tile counts and output frames
                // -> depthwise output frames = (frames + 2 * padding - dilation * (taps - 1) - 1) / stride + 1
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
                // -> bias then mult then shift (add layers get mult and shift for both inputs instead)
                // -> start pulses once they're all in so the unit runs while the weights stream behind it
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
                // -> weight beats go into the ping pong buffer and stall when both halves are full
                WEIGHTS: if (accept) begin
                    count <= count + 1;
                    if (count == words - 1) begin
                        last <= s_axis_tlast;
                        state <= FINISH;
                    end
                end
                // -> wait for the unit then let the pipelines drain so every result reaches the bank
                // -> the dest bank now holds this layer's output so remember its frames and sign for the next layer
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
                // -> the last layer is done so send the reply header
                REPLY: if (m_axis_tready) begin
                    count <= 0;
                    step <= 0;
                    state <= WAIT;
                end
                // -> give the ultraram LAT cycles to read the next logits word
                WAIT: begin
                    step <= step + 1;
                    if (step == LAT - 1) begin
                        state <= SEND;
                    end
                end
                // -> one logits word per beat (class by class with 16 frames each) then back to HEADER for the next run
                SEND: if (m_axis_tready) begin
                    count <= count + 1;
                    step <= 0;
                    state <= count == words - 1 ? HEADER : WAIT;
                end
                default: state <= HEADER;
            endcase
        end
    end

    // NOTE: units

    // bias and requant numbers for the channel being worked on
    assign param_ch = cfg.add ? add_ch : rq_ch;
    assign bias_otile = cfg.dw ? dw_tag.otile : a_tag.otile;
    stt_params parameters(
        aclk, state == PARAMS && accept, slot, 8'(count), s_axis_tdata, param_ch, params, bias_otile, bias);

    // bank 0 takes the input -> after that writes go to the layer's dest bank from the requantiser or the add
    // port a reads the layer's source bank -> port b reads an add's second input then the reply
    assign pool_we = state == INPUT ? accept : cfg.add ? add_we : rq_we;
    assign pool_bank = state == INPUT ? 2'd0 : cfg.dest;
    assign pool_address = state == INPUT ? AW'(count) : cfg.add ? add_address : rq_address;
    assign pool_data = state == INPUT ? s_axis_tdata : cfg.add ? add_data : rq_data;
    assign a_address = cfg.add ? add_address : cfg.dw ? dw_address : pw_address;
    assign adding = cfg.add && state == FINISH;
    assign b_bank = adding ? cfg.branch : cfg.dest;
    assign b_address = adding ? add_address : AW'(count);
    stt_act_pool pool(
        aclk, pool_we, pool_bank, pool_address, pool_data, cfg.source, b_bank, a_address, b_address, a_data, b_data);

    // the weight buffer is shared -> whichever unit runs this layer drives its read side
    assign w_half = cfg.dw ? dw_half : pw_half;
    assign w_k = cfg.dw ? dw_k : pw_k;
    assign w_release = cfg.dw ? dw_release : pw_release;
    stt_weight_buf weights(
        aclk, rst, cfg.kpt, s_axis_tdata, state == WEIGHTS && s_axis_tvalid, w_ready, w_half, w_k, w_data, w_full,
        w_release);

    // pointwise -> the controller feeds the 32 x 16 mac array one input channel per cycle
    stt_pw_ctrl pw(
        aclk, rst, start && !cfg.dw && !cfg.add, cfg.ci, cfg.nt, cfg.n_otile, cfg.valid, cfg.in_signed, w_full,
        pw_release, pw_half, pw_k, w_data, pw_address, a_data, a_valid, a_first, a_last, a_tag, a_weight, a_act,
        pw_done);
    stt_mac_array array(
        aclk, rst, a_valid, a_first, a_last, a_tag, a_weight, a_act, bias, array_valid, array_index, array_tag,
        array_sum);
    // depthwise -> 16 channels x 16 frames with one tap per cycle
    stt_dw_ctrl dw(
        aclk, rst, start && cfg.dw, cfg.nt, cfg.nt_out, cfg.co, cfg.taps, cfg.padding, cfg.stride, cfg.dilation,
        cfg.valid, cfg.in_signed, w_full, dw_release, dw_half, dw_k, w_data, dw_address, a_data, bias, dw_valid,
        dw_index, dw_tag, dw_sum, dw_done);
    // requantiser -> shared by pointwise and depthwise and takes whichever one is running
    stt_requant_row requant(
        aclk, rst, cfg.co, cfg.nt_out, cfg.relu, cfg.dw ? dw_valid : array_valid, cfg.dw ? dw_index : array_index,
        cfg.dw ? dw_tag : array_tag, cfg.dw ? dw_sum : array_sum, rq_ch, params[1], params[2], rq_we, rq_address,
        rq_data);
    // residual add -> reads both banks and writes the sum with no dsps
    stt_add_ctrl add(
        aclk, rst, start && cfg.add, cfg.co, cfg.nt, add_address, add_ch, a_data, b_data, params, add_we, add_data,
        add_done);
endmodule
