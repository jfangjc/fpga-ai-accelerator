// Simple dual-port RAM with LATENCY read cycles
module stt_sdp_ram #(
    parameter int WIDTH = 128,
    parameter int DEPTH = 1024,
    parameter int LATENCY = 2,
    parameter string STYLE = "block"
) (
    input logic clk, we,
    input logic [$clog2(DEPTH)-1:0] waddr,
    input logic [WIDTH-1:0] wdata,
    input logic [$clog2(DEPTH)-1:0] raddr,
    output logic [WIDTH-1:0] rdata
);
    logic [WIDTH-1:0] pipe[LATENCY];

    if (STYLE == "ultra") begin : g_ultra
        (* ram_style = "ultra" *) logic [WIDTH-1:0] mem[DEPTH];
        always_ff @(posedge clk) begin
            if (we) begin
                mem[waddr] <= wdata;
            end
            pipe[0] <= mem[raddr];
        end
    end
    else begin : g_block
        (* ram_style = "block" *) logic [WIDTH-1:0] mem[DEPTH];
        always_ff @(posedge clk) begin
            if (we) begin
                mem[waddr] <= wdata;
            end
            pipe[0] <= mem[raddr];
        end
    end

    always_ff @(posedge clk)
        for (int i = 1; i < LATENCY; i++) pipe[i] <= pipe[i-1];
    assign rdata = pipe[LATENCY-1];
endmodule
