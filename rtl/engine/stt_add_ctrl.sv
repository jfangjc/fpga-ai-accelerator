// Residual add uses the existing two activation banks, overwriting branch 1
// only after reading the word. Each 16-lane word is converted by a five-stage
// pipeline that accepts one (lane, branch) item per clock: multiply, magnitude,
// single rounding, INT16 clamp, then sum and UINT8 clamp. Branch 0 of a lane
// always reaches the last stage one clock before branch 1 of the same lane.
module stt_add_ctrl import stt_engine_pkg::*; (
    input logic clk, rst, start,
    input layer_cfg_t unused_cfg,
    input logic p_we,
    input logic [1:0] p_sel,
    input logic [7:0] p_addr,
    input logic [127:0] p_data,
    output logic re,
    output logic [ACT_AW-1:0] addr,
    input logic [127:0] a_data, b_data,
    output logic we,
    output logic [127:0] data,
    output logic done
);
    typedef enum logic [2:0] {IDLE, READ, WAIT_READ, CAPTURE, ISSUE, DRAIN, WRITE} state_t;
    localparam int ITEMS = 32;  // 16 lanes x 2 branches per word
    localparam int DEPTH = 4;   // clocks from the last issue to its final stage
    state_t state;
    // Wire order: multiplier 0, shift 0, multiplier 1, shift 1.
    logic [3:0][31:0] param_words;
    logic [9:0] ch;
    logic [5:0] tile;
    logic [4:0] item;
    logic [2:0] delay_count;
    logic [127:0] a, b;
    logic issue_branch;
    logic [3:0] issue_lane;
    // Pipeline registers. Lane and branch travel with each item.
    logic v0, v1, v2, v3;
    logic b0, b1, b2, b3;
    logic [3:0] l0, l1, l2, l3;
    logic signed [63:0] product0;
    logic signed [32:0] n0, n1;
    logic [63:0] magnitude1, rounded2;
    logic neg1, neg2;
    logic signed [31:0] converted3, saved, total;
    // Explicit independent 128-bit memories preserve the stream write width
    // and infer LUT RAM rather than a multidimensional register array.
    for (genvar slot=0; slot<4; slot++) begin : g_params
        (* ram_style = "distributed" *) logic [127:0] mem [256];
        logic [127:0] word_data;
        always_ff @(posedge clk)
            if (p_we && p_sel == 2'(slot)) mem[p_addr] <= p_data;
        assign word_data = mem[ch[9:2]];
        assign param_words[slot] = word_data[32*ch[1:0]+:32];
    end
    // Words are visited tile-major within channel (addr = ch*nt + tile), which is
    // simply sequential, so a counter replaces the multiply on the address path.
    logic [ACT_AW-1:0] word_addr;
    assign addr = word_addr;
    assign re = state == READ;
    assign we = state == WRITE;
    assign issue_branch = item[0];
    assign issue_lane = item[4:1];
    assign total = saved + converted3;

    // Stage 0: INT8 x positive Q31 product and the widened shift amount.
    // Widen before adding 31 so INT32_MAX shifts cannot wrap. The product of
    // INT8 and positive Q31 is bounded well below the INT64 limits.
    always_ff @(posedge clk) begin
        v0 <= state == ISSUE;
        b0 <= issue_branch;
        l0 <= issue_lane;
        product0 <= $signed(issue_branch ? b[8*issue_lane+:8] : a[8*issue_lane+:8])
                  * $signed(param_words[issue_branch ? 2 : 0]);
        n0 <= 33'sd31 + $signed({param_words[issue_branch ? 3 : 1][31],
                                 param_words[issue_branch ? 3 : 1]});
    end
    // Stage 1: magnitude and sign, so the rounding add is unsigned.
    always_ff @(posedge clk) begin
        v1 <= v0; b1 <= b0; l1 <= l0; n1 <= n0;
        neg1 <= product0[63];
        magnitude1 <= product0[63] ? 64'(-product0) : 64'(product0);
    end
    // Stage 2: the single rounding (half away from zero via magnitude).
    always_ff @(posedge clk) begin
        v2 <= v1; b2 <= b1; l2 <= l1; neg2 <= neg1;
        rounded2 <= (n1 >= 1 && n1 < 64) ? (magnitude1 + (64'd1 << (n1-1))) >> n1 : 64'd0;
    end
    // Stage 3: restore the sign and clamp the branch to INT16.
    always_ff @(posedge clk) begin
        v3 <= v2; b3 <= b2; l3 <= l2;
        if (neg2) converted3 <= rounded2 > 32768 ? -32'sd32768 : -32'(rounded2);
        else      converted3 <= rounded2 > 32767 ?  32'sd32767 :  32'(rounded2);
    end
    always_ff @(posedge clk) begin
        if (rst) begin state <= IDLE; done <= 0; end
        else begin
            // Stage 4: branch 0 is held; branch 1 completes the lane.
            if (v3) begin
                if (!b3) saved <= converted3;
                else data[8*l3+:8] <= total < 0 ? 8'd0 : total > 255 ? 8'd255 : 8'(total);
            end
            case (state)
                IDLE: if (start) begin ch<=0; tile<=0; word_addr<=0; data<=0; state<=READ; end
                READ: begin delay_count<=3'(ACT_LAT); state<=WAIT_READ; end
                WAIT_READ: if (delay_count==1) state<=CAPTURE; else delay_count<=delay_count-1;
                // Keep both source words until all lanes are converted. This
                // makes in-place replacement of the second branch safe.
                CAPTURE: begin a<=a_data; b<=b_data; item<=0; state<=ISSUE; end
                ISSUE: begin
                    if (item == 5'(ITEMS-1)) begin delay_count<=3'(DEPTH); state<=DRAIN; end
                    item <= item + 1;
                end
                DRAIN: if (delay_count==1) state<=WRITE; else delay_count<=delay_count-1;
                WRITE: if (tile+1 < unused_cfg.nt) begin tile<=tile+1; word_addr<=word_addr+1; state<=READ; end
                    else if (11'(ch)+1 < unused_cfg.co) begin ch<=ch+1; tile<=0; word_addr<=word_addr+1; state<=READ; end
                    else begin done<=1; state<=IDLE; end
                default: state<=IDLE;
            endcase
        end
    end
endmodule
