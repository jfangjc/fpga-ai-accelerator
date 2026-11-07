// Engine memories, each written and read back once
module tb_engine_memory;
import stt_engine_pkg::*;
logic clk = 0;
always #5 clk = ~clk;

logic we = 0;
logic [1:0] write_bank, a_bank, b_bank;
logic [AW - 1:0] write_address, a_address, b_address;
logic [127:0] write_data, a_data, b_data;
stt_act_banks banks(
    clk, we, write_bank, write_address, write_data, a_bank, b_bank, a_address, b_address, a_data, b_data);

logic w_rst = 1, wr_valid = 0, wr_ready;
logic [127:0] wr_data;
logic rd_half = 0;
logic [9:0] rd_k = 0;
logic [255:0] rd_data;
logic [1:0] full, release_half = 0;
stt_weight_buf weights(clk, w_rst, 7'd1, wr_data, wr_valid, wr_ready, rd_half, rd_k, rd_data, full, release_half);

logic p_we = 0;
logic [1:0] p_array;
logic [7:0] p_beat = 0;
logic [127:0] p_data;
logic [9:0] p_ch = 0;
logic [3:0][31:0] params;
logic [ROWS - 1:0][31:0] bias;
stt_layer_params parameters(clk, p_we, p_array, p_beat, p_data, p_ch, params, 5'd0, bias);

initial begin
    // Banks hold 1000 * bank at the first word and 1000 * bank + 1 at the last
    for (int k = 0; k < BANKS; k++) begin
        for (int i = 0; i < 2; i++) begin
            @(negedge clk);
            we = 1;
            write_bank = k;
            write_address = i ? BANK_WORDS - 1 : 0;
            write_data = 1000 * k + i;
        end
    end
    @(negedge clk);
    we = 0;
    for (int k = 0; k < BANKS; k++) begin
        a_bank = k;
        b_bank = (k + 1) % BANKS;
        a_address = 0;
        b_address = BANK_WORDS - 1;
        repeat (LAT) @(negedge clk);
        if (a_data !== 1000 * k || b_data !== 1000 * ((k + 1) % BANKS) + 1) begin
            $fatal(1, "FAIL banks: bank %0d", k);
        end
    end

    // Two 16-row weight tiles fill both halves, and beat j of tile t holds 100 * t + j
    w_rst = 0;
    for (int j = 0; j < 64; j++) begin
        @(negedge clk);
        wr_valid = 1;
        wr_data = 100 * (j / 32) + j % 32;
    end
    @(negedge clk);
    wr_valid = 0;
    if (full !== 2'b11 || wr_ready) begin
        $fatal(1, "FAIL weights: not full");
    end
    for (int h = 0; h < 2; h++) begin
        rd_half = h;
        rd_k = 15;
        repeat (LAT) @(negedge clk);
        if (rd_data !== {128'(100 * h + 31), 128'(100 * h + 30)}) begin
            $fatal(1, "FAIL weights: half %0d", h);
        end
    end
    release_half = 2'b01;
    @(negedge clk);
    release_half = 0;
    if (full !== 2'b10 || !wr_ready) begin
        $fatal(1, "FAIL weights: release");
    end

    // Parameter array i holds 10 * i + channel for channels 0 to 3, and array 0 is also the bias
    for (int i = 0; i < 4; i++) begin
        @(negedge clk);
        p_we = 1;
        p_array = i;
        p_data = {32'(10 * i + 3), 32'(10 * i + 2), 32'(10 * i + 1), 32'(10 * i)};
    end
    @(negedge clk);
    p_we = 0;
    for (int c = 0; c < 4; c++) begin
        p_ch = c;
        repeat (PARAM_LAT) @(negedge clk);
        for (int i = 0; i < 4; i++) begin
            if (params[i] !== 10 * i + c) begin
                $fatal(1, "FAIL params: array %0d channel %0d", i, c);
            end
        end
        if (bias[c] !== c) begin
            $fatal(1, "FAIL params: bias %0d", c);
        end
    end
    $display("PASS");
    $finish;
end
endmodule
