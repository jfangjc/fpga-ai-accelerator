module tb_control;
logic clk=0;always #5 clk=~clk;
logic resetn=0;logic signed [17:0] sample;logic valid,ready;logic [31:0] data;logic ov,orr,last,done;
logic [17:0] samples[0:6559];logic [63:0] expected[0:2559];integer sent=0,words=0,cycles=0;logic [31:0] lowword;logic [31:0] rng=123;
logic [11:0] ra=0;logic rv=0,rr=0,arready,rvalid;logic [31:0] rd;
logic awv=0,wv=0,bv,awr,wr;logic [11:0] addr=0;logic [31:0] wdata=0;logic irq;logic btn=0;logic led;
stt_frontend dut(.aclk(clk),.aresetn(resetn),
.s_axi_awaddr(addr),.s_axi_awprot(0),.s_axi_awvalid(awv),.s_axi_awready(awr),
.s_axi_wdata(wdata),.s_axi_wstrb(4'hf),.s_axi_wvalid(wv),.s_axi_wready(wr),.s_axi_bvalid(bv),.s_axi_bready(1'b1),
.s_axi_araddr(ra),.s_axi_arprot(0),.s_axi_arvalid(rv),.s_axi_rready(rr),.s_axi_arready(arready),.s_axi_rvalid(rvalid),.s_axi_rdata(rd),
.s_axis_samples_tdata({{14{sample[17]}},sample}),.s_axis_samples_tkeep(4'hf),.s_axis_samples_tlast(1'b0),.s_axis_samples_tvalid(valid),.s_axis_samples_tready(ready),
.m_axis_features_tdata(data),.m_axis_features_tvalid(ov),.m_axis_features_tready(orr),.m_axis_features_tlast(last),
.irq(irq),.pmod_i2s_dout(1'b0),.pmod_i2s2_dout(1'b0),.pmod_btn_sw1(btn),.pmod_led_d1(led));

assign sample=18'sd1234;assign valid=1;assign orr=0;
task automatic write_reg(input integer a,input integer value);
 @(negedge clk);addr=a;wdata=value;wv=1;
 do @(posedge clk);while(!wr);
 @(negedge clk);wv=0;repeat(4)@(negedge clk);awv=1;
 do @(posedge clk);while(!awr);
 @(negedge clk);awv=0;wait(bv);@(negedge clk);
endtask
task automatic read_reg(input integer a,output logic [31:0] val);
 @(negedge clk);ra=a;rv=1;
 do @(posedge clk);while(!arready);
 @(negedge clk);rv=0;wait(rvalid);val=rd;repeat(3)@(negedge clk);
 if(rd!==val||!rvalid)$fatal(1,"read backpressure");rr=1;@(negedge clk);rr=0;
endtask
logic [31:0] value;
integer clock_count=0,previous_accept=0,accepted=0;
logic check_pace=0,check_bulk=0;
always @(posedge clk)begin
 clock_count<=clock_count+1;
 if(!resetn)begin accepted<=0;previous_accept<=0;end
 else if(valid&&ready)begin
   if(accepted>0&&check_pace&&clock_count-previous_accept!=6250)$fatal(1,"16kHz pacing");
   if(accepted>0&&check_bulk&&clock_count-previous_accept!=1)$fatal(1,"bulk pacing");
   previous_accept<=clock_count;accepted<=accepted+1;
 end
end
initial begin
 #30;resetn=1;write_reg(12,3);read_reg(12,value);if(value!=3)$fatal(1,"register");
 write_reg(0,7);repeat(6000)@(negedge clk);
 read_reg(4,value);if(value[14:4]!=1024)$fatal(1,"FIFO capacity %d",value[14:4]);
 if(ready)$fatal(1,"FIFO backpressure");
 write_reg(0,1);repeat(6800000)@(negedge clk);
 read_reg(20,value);if(value==0)$fatal(1,"overrun counter");
 read_reg(4,value);if(!value[1])$fatal(1,"sticky overflow");
 write_reg(0,0);write_reg(8,2);read_reg(4,value);if(value[1])$fatal(1,"overflow clear");
 write_reg(0,192);if(!led)$fatal(1,"LED register");
 btn=1;repeat(100)@(negedge clk);btn=0;repeat(100)@(negedge clk);
 read_reg(4,value);if(value[3])$fatal(1,"button glitch");
 btn=1;repeat(1048600)@(negedge clk);
 read_reg(4,value);if(!value[3]||!value[0])$fatal(1,"button sticky/IRQ");
 write_reg(8,9);read_reg(4,value);if(value[3]||value[0])$fatal(1,"button IRQ clear");
 repeat(200)@(negedge clk);read_reg(4,value);if(value[3])$fatal(1,"button repeat");
 write_reg(0,0);if(led)$fatal(1,"LED clear");
 read_reg(24,value);if(value!=0)$fatal(1,"empty FIFO must stall, not underrun");
 @(negedge clk);resetn=0;repeat(3)@(negedge clk);resetn=1;
 check_pace=1;write_reg(0,15);wait(accepted==20);check_pace=0;
 @(negedge clk);resetn=0;repeat(3)@(negedge clk);resetn=1;
 check_bulk=1;write_reg(0,7);wait(accepted==20);check_bulk=0;
 $display("PASS injection paced 16kHz and bulk: 20 beats each");
 $display("PASS button glitch/debounce/sticky/IRQ/clear and LED");
 $display("PASS AXI independent AW/W, held read response, FIFO capacity/backpressure, mic overflow and W1C");$finish;
end
endmodule
