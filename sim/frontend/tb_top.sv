module tb_top;
logic clk=0;always #5 clk=~clk;
logic resetn=0;logic signed [17:0] sample;logic valid=0,ready;logic [31:0] data;logic ov,orr,last,done;
logic [17:0] samples[0:32159];logic [63:0] expected[0:12799];integer sent=0,words=0,cycles=0;logic [31:0] lowword;logic [31:0] rng=123;
logic awv=0,wv=0,bv,awr,wr;logic [11:0] addr=0;logic [31:0] wdata=0;logic irq;
stt_frontend dut(.aclk(clk),.aresetn(resetn),
.s_axi_awaddr(addr),.s_axi_awprot(0),.s_axi_awvalid(awv),.s_axi_awready(awr),
.s_axi_wdata(wdata),.s_axi_wstrb(4'hf),.s_axi_wvalid(wv),.s_axi_wready(wr),.s_axi_bvalid(bv),.s_axi_bready(1'b1),
.s_axi_araddr(0),.s_axi_arprot(0),.s_axi_arvalid(1'b0),.s_axi_rready(1'b1),
.s_axis_samples_tdata({{14{sample[17]}},sample}),.s_axis_samples_tkeep(4'hf),.s_axis_samples_tlast(1'b0),.s_axis_samples_tvalid(valid),.s_axis_samples_tready(ready),
.m_axis_features_tdata(data),.m_axis_features_tvalid(ov),.m_axis_features_tready(orr),.m_axis_features_tlast(last),
.irq(irq),.pmod_i2s_dout(1'b0),.pmod_i2s2_dout(1'b0),.pmod_btn_sw1(1'b0));
task automatic write_reg(input logic [11:0] address,input logic [31:0] value);
 @(negedge clk);addr=address;wdata=value;awv=1;
 do @(posedge clk);while(!awr);
 @(negedge clk);awv=0;wv=1;
 do @(posedge clk);while(!wr);
 @(negedge clk);wv=0;wait(bv);@(negedge clk);
endtask
integer irq_n=3;
initial begin
 if($value$plusargs("period=%d",irq_n))begin end
 wait(resetn);write_reg(12,irq_n);write_reg(0,7);
 for(int f=1;f<=200;f++)begin
   wait(dut.frames==f);@(negedge clk);
   if(irq !== (f%irq_n==0))$fatal(1,"IRQ period/clear frame %d",f);
   if(f%irq_n==0)write_reg(8,1);
 end
 $display("PASS IRQ period %0d: 200 frames, %0d IRQs with AXI W1C",irq_n,200/irq_n);$finish;
end

assign sample=samples[sent<32160?sent:0];assign orr=rng[7];
string vectors;  // stimulus directory: +vectors=DIR (make passes build/sim/frontend)
initial begin if(!$value$plusargs("vectors=%s",vectors))vectors="../../build/sim/frontend";$readmemh({vectors,"/samples.mem"},samples);$readmemh({vectors,"/expected.mem"},expected);#30;resetn=1;end
always @(negedge clk)begin
 if(!resetn)valid<=0;
 else if(!valid||ready)valid<=sent<32160&&rng[2];
end
always @(posedge clk)if(resetn)begin
 rng<=(rng>>1)^((0-(rng&1))&32'hd0000001);cycles<=cycles+1;
 if(valid&&ready)sent<=sent+1;
 if(ov&&orr)begin
 if(words%130==0)begin if(data!=words/130)$fatal(1,"sequence");end
 else if(words%130==1)begin if(data!=32'hffffffd6)$fatal(1,"exponent");end
 else if(words%2==0)lowword<=data;
 else if({data,lowword}!==expected[(words/130)*64+(words%130-2)/2])$fatal(1,"mismatch word %0d got %h expected %h",words,{data,lowword},expected[(words/130)*64+(words%130-2)/2]);
 if(last!=(words%130==129))$fatal(1,"last");
 words<=words+1;if(words==25999)begin  $display("PASS 200 frames 12800 bit-exact bands, %0d cycles",cycles);end end
 if(cycles>50000000)$fatal(1,"timeout");end
endmodule
