// Engine compute units with fixed inputs and expected outputs
module tb_engine_compute;
import stt_engine_pkg::*;
logic clk = 0, rst = 1;
always #5 clk = ~clk;

logic m_valid = 0, m_first, m_last;
tile_tag_t m_tag = '{otile: 3, base: 7};
logic [ROWS - 1:0][7:0] m_weight;
logic [FRAMES - 1:0][8:0] m_act;
logic [ROWS - 1:0][31:0] m_bias;
logic row_valid;
logic [4:0] row_index;
tile_tag_t row_tag;
logic [FRAMES - 1:0][31:0] row_sum;
stt_pw_array mac(
    clk, rst, m_valid, m_first, m_last, m_tag, m_weight, m_act, m_bias, row_valid, row_index, row_tag, row_sum);

// Multiplier 2^30 and shift 0 halve the sum
logic rq_relu, rq_valid = 0;
tile_tag_t rq_tag = '{otile: 0, base: 5};
logic [FRAMES - 1:0][31:0] rq_sum;
logic [9:0] rq_ch;
logic rq_we;
logic [AW - 1:0] rq_address;
logic [127:0] rq_data;
stt_requant requantiser(
    clk, rst, 11'd40, 6'd3, rq_relu, rq_valid, 5'd2, rq_tag, rq_sum, rq_ch, 32'h40000000, 0, rq_we,
    rq_address, rq_data);

// Multiplier 2^30 with shift -2 doubles input A, and with shift 0 halves input B
logic add_start = 0, add_we, add_done;
logic [AW - 1:0] add_address;
logic [9:0] add_ch;
logic [127:0] a_data, b_data, add_data;
logic [3:0][31:0] add_params = {32'd0, 32'h40000000, -32'sd2, 32'h40000000};
stt_add_ctrl adder(
    clk, rst, add_start, 11'd1, 6'd1, add_address, add_ch, a_data, b_data, add_params, add_we, add_data, add_done);

// Rounding is half away from zero
int sums[16] = '{3, -3, 5, -5, 0, 1, -1, 254, 255, 256, 300, -300, 600, -600, 100, -100};
int int8[16] = '{2, -2, 3, -3, 0, 1, -1, 127, 127, 127, 127, -128, 127, -128, 50, -50};
int uint8[16] = '{2, 0, 3, 0, 0, 1, 0, 127, 128, 128, 150, 0, 255, 0, 50, 0};
int a_in[16] = '{100, -100, 127, -128, 0, 127, 10, 20, 1, 2, 3, 4, 5, 6, 7, 8};
int b_in[16] = '{50, 50, 127, -128, 0, 1, -10, -5, 1, 2, 3, 4, 5, 6, 7, 8};
int add_out[16] = '{225, 0, 255, 0, 0, 255, 15, 37, 3, 5, 8, 10, 13, 15, 18, 20};

initial begin
    repeat (2) @(negedge clk);
    rst = 0;

    // 32 cycles of weight o - 16 by activation t - 8 on top of bias 100 * o
    for (int o = 0; o < ROWS; o++) begin
        m_weight[o] = o - 16;
        m_bias[o] = 100 * o;
    end
    for (int t = 0; t < FRAMES; t++) begin
        m_act[t] = t - 8;
    end
    for (int j = 0; j < 32; j++) begin
        @(negedge clk);
        m_valid = 1;
        m_first = j == 0;
        m_last = j == 31;
    end
    @(negedge clk);
    m_valid = 0;
    while (!row_valid) begin
        @(negedge clk);
    end
    for (int o = 0; o < ROWS; o++) begin
        if (row_index !== o || row_tag !== m_tag) begin
            $fatal(1, "FAIL array: row %0d", o);
        end
        for (int t = 0; t < FRAMES; t++) begin
            if ($signed(row_sum[t]) !== 100 * o + 32 * (o - 16) * (t - 8)) begin
                $fatal(1, "FAIL array: row %0d frame %0d", o, t);
            end
        end
        @(negedge clk);
    end

    // One row as INT8, then as UINT8, written to base 5 + channel 2 * 3 tiles
    for (int relu = 0; relu < 2; relu++) begin
        @(negedge clk);
        rq_relu = relu;
        rq_valid = 1;
        for (int t = 0; t < FRAMES; t++) begin
            rq_sum[t] = sums[t];
        end
        @(negedge clk);
        rq_valid = 0;
        while (!rq_we) begin
            @(negedge clk);
        end
        for (int t = 0; t < FRAMES; t++) begin
            if (rq_address !== 11 || rq_data[8 * t+:8] !== 8'(relu ? uint8[t] : int8[t])) begin
                $fatal(1, "FAIL requant: relu %0d frame %0d", relu, t);
            end
        end
    end

    // One word of 2 * a + b / 2 clamped to UINT8
    for (int l = 0; l < 16; l++) begin
        a_data[8 * l+:8] = a_in[l];
        b_data[8 * l+:8] = b_in[l];
    end
    @(negedge clk);
    add_start = 1;
    @(negedge clk);
    add_start = 0;
    while (!add_we) begin
        @(negedge clk);
    end
    for (int l = 0; l < 16; l++) begin
        if (add_address !== 0 || add_data[8 * l+:8] !== add_out[l]) begin
            $fatal(1, "FAIL add: lane %0d", l);
        end
    end
    $display("PASS");
    $finish;
end
endmodule
