// 64 mel bands from the FFT power, sent as 128 words
module stt_mel
    import stt_tables_pkg::*;
(
    input logic clk, resetn, start,
    output logic [8:0] bin,
    input logic [63:0] bin_data,
    output logic next,
    output logic [31:0] data,
    output logic out_valid, last,
    input logic out_ready
);
    localparam int LATENCY = 1;

    typedef enum logic [2:0] {IDLE, CLEAR, READ, WAIT, MULTIPLY, SEND_READ, SEND} state_t;
    state_t state;

    (* rom_style = "block" *) logic [61:0] mel[0:256];
    logic [91:0] bands[0:63];
    initial mel = mel_table();
    logic [61:0] mel_q;
    logic [91:0] band_q;

    logic [8:0] n;
    logic [1:0] op;
    logic [2:0] wait_count;
    logic [6:0] word;
    logic signed [31:0] re, im, x_r;
    logic signed [63:0] square;
    logic [63:0] power, power_r, band;
    logic [27:0] weight, weight_r;
    logic [91:0] pw;
    logic [5:0] band_address;

    assign bin = n;
    assign square = x_r * x_r;
    assign weight = op == 2 ? mel_q[27:0] : mel_q[55:28];
    assign pw = power_r * weight_r;

    assign band_address = state == SEND_READ || state == SEND ? word[6:1] : mel_q[61:56] + 6'(op == 3);
    assign band = band_q[91:28];
    assign data = word[0] ? band[63:32] : band[31:0];
    assign out_valid = state == SEND;
    assign last = word == 127;
    assign next = out_valid && out_ready && last;

    always_ff @(posedge clk) begin
        mel_q <= mel[n];
        band_q <= bands[band_address];
        x_r <= op == 0 ? re : im;
        power_r <= power;
        weight_r <= weight;
    end

    always_ff @(posedge clk) begin
        if (!resetn) begin
            state <= IDLE;
            n <= 0;
            word <= 0;
            op <= 0;
            wait_count <= 0;
        end
        else begin
            case (state)
                IDLE:
                if (start) begin
                    n <= 0;
                    state <= CLEAR;
                end
                CLEAR: begin
                    bands[6'(n)] <= 0;
                    if (n == 63) begin
                        n <= 0;
                        state <= READ;
                    end
                    else begin
                        n <= n + 1;
                    end
                end
                READ: state <= WAIT;
                WAIT: begin
                    {im, re} <= bin_data;
                    op <= 0;
                    state <= MULTIPLY;
                end
                MULTIPLY:
                if (wait_count != LATENCY) begin
                    wait_count <= wait_count + 1;
                end
                else begin
                    wait_count <= 0;
                    op <= op + 1;
                    case (op)
                        0: power <= square;
                        1: power <= power + square;
                        2: bands[mel_q[61:56]] <= band_q + pw;
                        default: begin
                            // The top band has no upper neighbour
                            if (mel_q[61:56] != 63) begin
                                bands[mel_q[61:56]+1] <= band_q + pw;
                            end
                            if (n == 256) begin
                                word <= 0;
                                state <= SEND_READ;
                            end
                            else begin
                                n <= n + 1;
                                state <= READ;
                            end
                        end
                    endcase
                end
                SEND_READ: state <= SEND;
                SEND:
                if (out_ready) begin
                    if (word == 127) begin
                        state <= IDLE;
                    end
                    else begin
                        word <= word + 1;
                        state <= SEND_READ;
                    end
                end
                default: state <= IDLE;
            endcase
        end
    end
endmodule
