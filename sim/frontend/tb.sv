module tb;
logic clk=0;always #5 clk=~clk;
logic resetn=0;logic signed [17:0] sample;logic valid=0,ready;logic [31:0] data;logic ov,orr,last,done;
logic [17:0] samples[0:32159];logic [63:0] expected[0:12799];integer sent=0,words=0,cycles=0;logic [31:0] lowword;logic [31:0] rng=123;
stt_fft_mel dut(clk,resetn,sample,valid,ready,data,ov,orr,last,done);
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
 words<=words+1;if(words==25999)begin $display("PASS 200 frames 12800 bit-exact bands, %0d cycles",cycles);$finish;end end
 if(cycles>50000000)$fatal(1,"timeout");end
endmodule
