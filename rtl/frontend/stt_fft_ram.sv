// Registered-read true dual-port storage. No memory reset: the framer
// initializes all FFT locations before the first butterfly is issued.
module stt_fft_ram (
    input logic clk,
    input logic [8:0] address_a, address_b,
    input logic write_a, write_b,
    input logic [63:0] data_a, data_b,
    output logic [63:0] q_a, q_b
);
    (* ram_style = "block" *) logic [63:0] memory [0:511];
    always_ff @(posedge clk) begin
        if (write_a) memory[address_a] <= data_a;
        q_a <= memory[address_a];
    end
    always_ff @(posedge clk) begin
        if (write_b) memory[address_b] <= data_b;
        q_b <= memory[address_b];
    end
endmodule
