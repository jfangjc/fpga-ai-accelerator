// Simple dual-port RAM: one synchronous write port and one read port whose
// data appears LATENCY (>= 1) cycles after re. Written in the Vivado
// inference style; STYLE selects "ultra" (UltraRAM), "block" or
// "distributed". The extra read stages become URAM/BRAM output and cascade
// registers. Read-during-write to the same address returns old data.
module stt_sdp_ram #(
    parameter int WIDTH = 128,
    parameter int DEPTH = 1024,
    parameter int LATENCY = 2,
    parameter string STYLE = "block",
    localparam int AW = $clog2(DEPTH)
) (
    input  logic             clk,
    input  logic             we,
    input  logic [AW-1:0]    waddr,
    input  logic [WIDTH-1:0] wdata,
    input  logic             re,
    input  logic [AW-1:0]    raddr,
    output logic [WIDTH-1:0] rdata
);
    logic [WIDTH-1:0] pipe [LATENCY];

    if (STYLE == "ultra") begin : g_ultra
        (* ram_style = "ultra" *) logic [WIDTH-1:0] mem [DEPTH];
        always_ff @(posedge clk) if (we) mem[waddr] <= wdata;
        always_ff @(posedge clk) if (re) pipe[0] <= mem[raddr];
    end else if (STYLE == "distributed") begin : g_lut
        (* ram_style = "distributed" *) logic [WIDTH-1:0] mem [DEPTH];
        always_ff @(posedge clk) if (we) mem[waddr] <= wdata;
        always_ff @(posedge clk) if (re) pipe[0] <= mem[raddr];
    end else begin : g_block
        (* ram_style = "block" *) logic [WIDTH-1:0] mem [DEPTH];
        always_ff @(posedge clk) if (we) mem[waddr] <= wdata;
        always_ff @(posedge clk) if (re) pipe[0] <= mem[raddr];
    end

    always_ff @(posedge clk) begin
        for (int i = 1; i < LATENCY; i++) pipe[i] <= pipe[i-1];
    end
    assign rdata = pipe[LATENCY-1];
endmodule
