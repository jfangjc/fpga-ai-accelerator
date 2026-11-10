module tb_beam;
 logic clk=0;always #5 clk=~clk;
 logic resetn=0,valid=0;
 logic signed [17:0] mic1,mic2,sample;
 logic [7:0] delay1,delay2;
 logic signed [15:0] gain;
 logic [53:0] vectors[0:4095];
 stt_beamformer dut(clk,resetn,valid,mic1,mic2,delay1,delay2,gain,sample);
 string vector_dir;  // +vectors=DIR (make passes build/sim/frontend)
 initial begin
  if(!$value$plusargs("vectors=%s",vector_dir))vector_dir="../../build/sim/frontend";
  $readmemh({vector_dir,"/beam.mem"},vectors);
  for(int config_id=0;config_id<4;config_id++)begin
   resetn=0;repeat(3)@(negedge clk);resetn=1;
   case(config_id)
    0:begin delay1=0;delay2=0;gain=16384;end
    1:begin delay1=1;delay2=5;gain=-8192;end
    2:begin delay1=17;delay2=63;gain=32767;end
    3:begin delay1=255;delay2=128;gain=-32768;end
   endcase
   for(int n=0;n<1024;n++)begin
    {mic2,mic1}=vectors[config_id*1024+n][35:0];
    repeat(3)@(negedge clk);
    if(n>=256&&sample!==vectors[config_id*1024+n][53:36])$fatal(1,"beam config %d sample %d",config_id,n);
    valid=1;@(negedge clk);valid=0;
   end
  end
  $display("PASS beam: 4 delay/gain configurations, both mics, 3072 bit-exact samples");$finish;
 end
endmodule
