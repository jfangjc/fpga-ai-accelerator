// Engine: a small QuartzNet-like network, twice
module tb_engine_network;
`include "tb_engine.svh"

spec_t network[$] = {
    depthwise(0, 5, 2, 1, 2, 1), pointwise(1, 40, 1, 2), depthwise(2, 7, 1, 2, 6, 0), pointwise(3, 40, 0, 1),
    pointwise(2, 40, 0, 0), add(4, 5, 2), pointwise(6, 29, 0, 0)};

initial begin
    reset_dut();
    run("network, 37 frames", 24, 37, 30, network);
    run("network, 64 frames", 24, 64, 64, network);
    $display("PASS");
    $finish;
end
endmodule
