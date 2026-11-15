// Engine: each layer kind on its own
module tb_engine_layers;
`include "tb_engine.svh"

initial begin
    reset_dut();
    run("pointwise, 40 to 48 channels, ReLU", 40, 40, 33, {pointwise(0, 48, 1, 1)});
    run("pointwise, 24 to 36 to 20 channels", 24, 24, 24, {pointwise(0, 36, 1, 1), pointwise(1, 20, 0, 2)});
    run("depthwise, 5 taps, stride 2", 24, 37, 30, {depthwise(0, 5, 2, 1, 2, 1)});
    run("depthwise, 7 taps, dilation 2", 24, 37, 37, {pointwise(0, 40, 1, 1), depthwise(1, 7, 1, 2, 6, 2)});
    run("residual add", 24, 21, 18, {pointwise(0, 24, 0, 1), pointwise(0, 24, 0, 2), add(1, 2, 0)});
    $display("PASS");
    $finish;
end
endmodule
