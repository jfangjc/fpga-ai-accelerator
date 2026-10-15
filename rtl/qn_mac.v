`timescale 1ns/1ps
// Thirty-two signed INT8 MAC lanes. All addresses below are byte addresses.
// 0000 CONTROL: write bit0 START, bit1 clear DONE/ERROR
// 0004 STATUS: bit0 BUSY, bit1 DONE, bit2 ERROR (invalid LENGTH)
// 0008 LENGTH: number of INT8 elements, 1..4096
// 000c BIAS: signed INT32 initial accumulator (also enables chunk chaining)
// 0010 CONFIG: [4:0] arithmetic right shift, [8] enable ReLU
// 0014 RESULT: sign-extended saturated INT8 result
// 0018 ACCUM: raw signed INT32 accumulator
// 1000..1ffc INPUT and 2000..2ffc WEIGHT: write-only packed INT8 RAMs.
// Lowest-address element is byte [7:0]. Last word's unused lanes are masked.
// Writes while BUSY are ignored, except CONTROL clear flags. RAM is not reset;
// software must write every word participating in a command before START.
// Accumulation uses two's-complement INT32 wrapping, as a quantized primitive;
// host must select scales/chunks that avoid INT32 overflow. Shift truncates
// arithmetically (toward negative infinity), then clamps to [-128,127]/[0,127].
module qn_mac (
    input wire clk,
    input wire rst,
    input wire wr_en,
    input wire [15:0] rd_addr,
    input wire [15:0] wr_addr,
    input wire [31:0] wr_data,
    output reg [31:0] rd_data
);
    localparam IDLE=2'd0, FETCH=2'd1, MAC=2'd2, FINISH=2'd3;
    reg [1:0] state;
    wire busy = state != IDLE;
    reg done, error;
    reg [12:0] length;
    reg signed [31:0] bias, accumulator;
    reg [8:0] config_reg;
    reg [6:0] row_index;
    reg [12:0] remaining;
    reg signed [7:0] result;
    wire signed [15:0] term [0:31];
    wire signed [16:0] sum2 [0:15];
    wire signed [17:0] sum4 [0:7];
    wire signed [18:0] sum8 [0:3];
    wire signed [19:0] sum16 [0:1];
    wire signed [20:0] dot32 = {sum16[0][19],sum16[0]} +
                              {sum16[1][19],sum16[1]};
    wire signed [31:0] shifted = accumulator >>> config_reg[4:0];

    // Eight interleaved packed-word banks provide 32 elements per FETCH.
    // CPU word-address bits [4:2] select bank and [11:5] select row.
    // XPM instances force block RAM implementation; memory is not reset.
    genvar bank, lane, tree;
    generate
        for (bank=0; bank<8; bank=bank+1) begin: banks
            wire [31:0] input_word, weight_word;
            wire selected = !rst && wr_en && !busy &&
                            wr_addr[1:0] == 0 && wr_addr[4:2] == bank;
            wire input_we = selected && wr_addr >= 16'h1000 && wr_addr <= 16'h1ffc;
            wire weight_we = selected && wr_addr >= 16'h2000 && wr_addr <= 16'h2ffc;
            xpm_memory_sdpram #(
                .MEMORY_SIZE(4096), .MEMORY_PRIMITIVE("block"),
                .CLOCKING_MODE("common_clock"), .ECC_MODE("no_ecc"),
                .USE_MEM_INIT(0), .WRITE_DATA_WIDTH_A(32),
                .BYTE_WRITE_WIDTH_A(32), .ADDR_WIDTH_A(7),
                .READ_DATA_WIDTH_B(32), .ADDR_WIDTH_B(7),
                .READ_LATENCY_B(1), .WRITE_MODE_B("read_first"),
                .SIM_ASSERT_CHK(1)
            ) input_mem (
                .clka(clk), .clkb(clk), .sleep(1'b0),
                .ena(input_we), .wea(input_we), .addra(wr_addr[11:5]),
                .dina(wr_data), .injectsbiterra(1'b0), .injectdbiterra(1'b0),
                .rstb(1'b0), .enb(state == FETCH), .regceb(1'b1),
                .addrb(row_index), .doutb(input_word), .sbiterrb(), .dbiterrb()
            );
            xpm_memory_sdpram #(
                .MEMORY_SIZE(4096), .MEMORY_PRIMITIVE("block"),
                .CLOCKING_MODE("common_clock"), .ECC_MODE("no_ecc"),
                .USE_MEM_INIT(0), .WRITE_DATA_WIDTH_A(32),
                .BYTE_WRITE_WIDTH_A(32), .ADDR_WIDTH_A(7),
                .READ_DATA_WIDTH_B(32), .ADDR_WIDTH_B(7),
                .READ_LATENCY_B(1), .WRITE_MODE_B("read_first"),
                .SIM_ASSERT_CHK(1)
            ) weight_mem (
                .clka(clk), .clkb(clk), .sleep(1'b0),
                .ena(weight_we), .wea(weight_we), .addra(wr_addr[11:5]),
                .dina(wr_data), .injectsbiterra(1'b0), .injectdbiterra(1'b0),
                .rstb(1'b0), .enb(state == FETCH), .regceb(1'b1),
                .addrb(row_index), .doutb(weight_word), .sbiterrb(), .dbiterrb()
            );
            for (lane=0; lane<4; lane=lane+1) begin: lanes
                (* use_dsp = "yes" *) wire signed [15:0] product =
                    $signed(input_word[lane*8 +: 8]) *
                    $signed(weight_word[lane*8 +: 8]);
                assign term[bank*4+lane] = remaining > bank*4+lane ?
                    product : 16'sd0;
            end
        end
        // Balanced reduction tree avoids a 32-adder serial dependency.
        for (tree=0; tree<16; tree=tree+1) begin: level2
            assign sum2[tree] = {term[tree*2][15],term[tree*2]} +
                               {term[tree*2+1][15],term[tree*2+1]};
        end
        for (tree=0; tree<8; tree=tree+1) begin: level4
            assign sum4[tree] = {sum2[tree*2][16],sum2[tree*2]} +
                               {sum2[tree*2+1][16],sum2[tree*2+1]};
        end
        for (tree=0; tree<4; tree=tree+1) begin: level8
            assign sum8[tree] = {sum4[tree*2][17],sum4[tree*2]} +
                               {sum4[tree*2+1][17],sum4[tree*2+1]};
        end
        for (tree=0; tree<2; tree=tree+1) begin: level16
            assign sum16[tree] = {sum8[tree*2][18],sum8[tree*2]} +
                                {sum8[tree*2+1][18],sum8[tree*2+1]};
        end
    endgenerate

    always @* begin
        rd_data = 32'd0;
        case (rd_addr)
            16'h0004: rd_data = {29'd0,error,done,busy};
            16'h0008: rd_data = {19'd0,length};
            16'h000c: rd_data = bias;
            16'h0010: rd_data = {23'd0,config_reg};
            16'h0014: rd_data = {{24{result[7]}},result};
            16'h0018: rd_data = accumulator;
            default: rd_data = 32'd0;
        endcase
    end

    always @(posedge clk) begin
        if (rst) begin
            state <= IDLE;
            done <= 0;
            error <= 0;
            length <= 0;
            bias <= 0;
            config_reg <= 0;
            accumulator <= 0;
            result <= 0;
            row_index <= 0;
            remaining <= 0;
        end else begin
            if (wr_en && wr_addr == 16'h0000 && wr_data[1]) begin
                done <= 0;
                error <= 0;
            end
            case (state)
                IDLE: begin
                    if (wr_en) begin
                        case (wr_addr)
                            16'h0008: length <= (wr_data <= 4096) ? wr_data[12:0] : 13'd0;
                            16'h000c: bias <= wr_data;
                            16'h0010: config_reg <= wr_data[8:0];
                            16'h0000: if (wr_data[0]) begin
                                done <= 0;
                                if (length == 0 || length > 4096) begin
                                    error <= 1;
                                end else begin
                                    error <= 0;
                                    accumulator <= bias;
                                    remaining <= length;
                                    row_index <= 0;
                                    state <= FETCH;
                                end
                            end
                            default: begin end
                        endcase
                    end
                end
                FETCH: state <= MAC;
                MAC: begin
                    accumulator <= accumulator + dot32;
                    if (remaining <= 32) begin
                        state <= FINISH;
                    end else begin
                        remaining <= remaining - 32;
                        row_index <= row_index + 1;
                        state <= FETCH;
                    end
                end
                FINISH: begin
                    if (config_reg[8] && shifted < 0) result <= 0;
                    else if (shifted > 127) result <= 8'sd127;
                    else if (shifted < -128) result <= -8'sd128;
                    else result <= shifted[7:0];
                    done <= 1;
                    state <= IDLE;
                end
                default: state <= IDLE;
            endcase
        end
    end
endmodule
