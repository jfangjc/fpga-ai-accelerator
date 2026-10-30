// Front end testbench harness
logic clk = 0, resetn = 0;
always #5 clk = ~clk;
logic signed [15:0] sample;
logic sample_valid, sample_ready, button, led;
logic [31:0] features;
logic features_valid, features_ready, features_last;
logic [11:0] awaddr = 0, araddr = 0;
logic [31:0] wdata = 0, rdata;
logic awvalid = 0, wvalid = 0, arvalid = 0, rready = 0, awready, wready, bvalid, arready, rvalid;

stt_frontend dut(
    .aclk(clk), .aresetn(resetn),
    .s_axi_awaddr(awaddr), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
    .s_axi_wdata(wdata), .s_axi_wvalid(wvalid), .s_axi_wready(wready),
    .s_axi_bvalid(bvalid), .s_axi_bready(1'b1),
    .s_axi_araddr(araddr), .s_axi_arvalid(arvalid), .s_axi_arready(arready),
    .s_axi_rdata(rdata), .s_axi_rvalid(rvalid), .s_axi_rready(rready),
    .s_axis_samples_tdata(sample), .s_axis_samples_tvalid(sample_valid), .s_axis_samples_tready(sample_ready),
    .m_axis_features_tdata(features), .m_axis_features_tlast(features_last),
    .m_axis_features_tvalid(features_valid), .m_axis_features_tready(features_ready),
    .pmod_i2s_bclk(), .pmod_i2s_lrclk(), .pmod_i2s_dout(1'b0), .pmod_led_d1(led), .pmod_btn_sw1(button));

task automatic write_reg(input logic [11:0] address, input logic [31:0] value);
    @(negedge clk);
    wdata = value;
    wvalid = 1;
    repeat (3) @(negedge clk);
    awaddr = address;
    awvalid = 1;
    do @(posedge clk); while (!(awready && wready));
    @(negedge clk);
    awvalid = 0;
    wvalid = 0;
    wait (bvalid);
    @(negedge clk);
endtask

task automatic read_reg(input logic [11:0] address, output logic [31:0] value);
    @(negedge clk);
    araddr = address;
    arvalid = 1;
    do @(posedge clk); while (!arready);
    @(negedge clk);
    arvalid = 0;
    wait (rvalid);
    value = rdata;
    repeat (3) @(negedge clk);
    if (!rvalid || rdata !== value) begin
        $fatal(1, "FAIL read response not held");
    end
    rready = 1;
    @(negedge clk);
    rready = 0;
endtask

task automatic reset_dut();
    @(negedge clk);
    resetn = 0;
    repeat (3) @(negedge clk);
    resetn = 1;
endtask
