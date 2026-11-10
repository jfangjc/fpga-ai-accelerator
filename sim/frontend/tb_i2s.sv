module tb_i2s;
logic clk=0;always #5 clk=~clk;
logic resetn=0,channel=0,bclk,lrclk,d1=0,d2=0,valid;logic signed [17:0] m1,m2;
integer bitpos=0,edges=0,received=0,cycles=0;logic old=0;
logic [23:0] a=24'h654300,b=24'hb12300;
stt_i2s dut(clk,resetn,d1,d2,channel,bclk,lrclk,m1,m2,valid);
initial begin #30;resetn=1;end
always @(negedge bclk)if(resetn)begin
 bitpos=(bitpos+1)%64;
 d1=(bitpos%32>=1&&bitpos%32<=24)?a[24-bitpos%32]:0;
 d2=(bitpos%32>=1&&bitpos%32<=24)?b[24-bitpos%32]:0;
end
always @(posedge clk)if(resetn)begin
 cycles<=cycles+1;old<=bclk;if(bclk&&!old)edges<=edges+1;
 if(valid)begin if(m1!==a[23:6]||m2!==b[23:6])$fatal(1,"capture %h %h",m1,m2);received<=received+1;if(received==5)channel<=1;end
 if(cycles==100000)begin if(edges<1014||edges>1034||received<10)$fatal(1,"clock/count");$display("PASS I2S both mics/channels, %0d rising edges in 1ms",edges);$finish;end
end
endmodule
