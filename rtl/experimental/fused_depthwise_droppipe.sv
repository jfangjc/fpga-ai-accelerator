`timescale 1ns/1ps
// Experimental channel-major depthwise stage. One channel is retained locally;
// output beats can write the pointwise input bank without a host round trip.
// Odd symmetric kernels, stride/dilation one. 64 time lanes per tap, pipelined rotation.
// For T<=16, each group of sixteen MAC lanes handles a separate channel.
// Longer inputs retain the original one-channel / 64-time-lane schedule.
module fused_depthwise_droppipe #(parameter MAX_T=256, MAX_KERNEL=41, LANES=64)(
 input wire aclk,aresetn,
 input wire cfg_valid,output wire cfg_ready,
 input wire [12:0] cfg_times,cfg_valid_times,
 input wire [6:0] cfg_kernel,
 input wire [4*MAX_KERNEL*8-1:0] cfg_weights,
 input wire [4*32-1:0] cfg_bias,cfg_multiplier,
 input wire [4*6-1:0] cfg_shift,input wire [4*8-1:0] cfg_xzero,cfg_yzero,
 input wire [LANES*8-1:0] s_data,input wire s_valid,output wire s_ready,
 output wire [LANES*8-1:0] m_data,output wire m_valid,input wire m_ready,
 output wire m_last,output reg error,output reg done
);
 localparam LW=$clog2(LANES), AW=$clog2(MAX_T/LANES), IDLE=0, INPUTS=1, CLEAR=2, MAC=3, QUANT=4;
 reg [2:0] state;
 reg [12:0] times,valid_times;
 reg [6:0] kernel,tap,read_tap;
 reg [4*MAX_KERNEL*8-1:0] weights;
 reg [4*32-1:0] bias,multiplier;
 reg [4*6-1:0] shift;
 reg [4*8-1:0] xzero,yzero;
 reg short_mode;
 wire quad_mode=short_mode && times<=16;
 wire dual_mode=short_mode && times>16;
 wire [3:0] bad_config;
 reg [AW:0] tiles,write_tile,time_tile;
 reg read_valid,product_valid,product_last;
 reg [2:0] quant_sent,quant_received;reg quant_done;reg [LANES*8-1:0] quant_buffer;
 wire [12:0] remaining_times=times-time_tile*LANES;
 wire [2:0] quant_chunks=short_mode?4:remaining_times>=LANES?4:(remaining_times+15)>>4;
 wire [1:0] quant_channel=quad_mode?quant_sent[1:0]:dual_mode?{1'b0,quant_sent[1]}:2'd0;
 reg [LW-1:0] read_rotation;
 reg [LANES-1:0] read_inside;
 reg signed [17:0] products[0:LANES-1];
 reg signed [31:0] accum[0:LANES-1];
 wire [LANES*8-1:0] bank_data;
 wire issue=state==MAC && tap<kernel;
 wire signed [17:0] base=$signed({1'b0,time_tile,{LW{1'b0}}})+$signed({1'b0,tap})-$signed({1'b0,kernel>>1});
 wire [LW-1:0] rotation=base[LW-1:0];
 wire [16*32-1:0] qa,qb,qm;
 wire [16*6-1:0] qs;
 wire [16*8-1:0] qz,qdata;
 wire qvalid,qready,qerror;
 assign cfg_ready=aresetn && state==IDLE;
 assign s_ready=aresetn && state==INPUTS;
 assign m_valid=aresetn && state==QUANT && quant_done;
 assign m_last=m_valid && time_tile==tiles-1;
 genvar b;
 generate for(b=0;b<4;b=b+1)begin: cfg_checks
  assign bad_config[b]=(cfg_times<=16 || (cfg_times<=32 && b<2) || b==0) &&
    (cfg_multiplier[b*32+31] || |cfg_multiplier[b*32+:7] || cfg_shift[b*6+:6]==0 || cfg_shift[b*6+:6]==63);
 end endgenerate
 generate for(b=0;b<LANES;b=b+1) begin: banks
  (* ram_style="distributed" *) reg [7:0] memory[0:MAX_T/LANES-1];
  reg [7:0] read_byte;
  wire [LW-1:0] displacement=b-rotation;
  wire signed [17:0] position=base+$signed({1'b0,displacement});
  always @(posedge aclk) begin
   if(s_valid && s_ready)memory[write_tile[AW-1:0]]<=s_data[b*8+:8];
   if(issue)read_byte<=memory[short_mode?0:position[AW+LW-1:LW]];
  end
  assign bank_data[b*8+:8]=read_byte;
  // Mask invalid output positions after requantization, in the UINT8 domain.
  assign m_data[b*8+:8]=(time_tile*LANES+(quad_mode?(b%16):dual_mode?(b%32):b)<valid_times)?
    quant_buffer[b*8+:8]:yzero[(quad_mode?(b/16):dual_mode?(b/32):0)*8+:8];
 end endgenerate
 generate for(b=0;b<16;b=b+1)begin: quant_lanes
  assign qa[b*32+:32]=accum[quant_sent[1:0]*16+b];assign qb[b*32+:32]=bias[quant_channel*32+:32];
  assign qm[b*32+:32]=multiplier[quant_channel*32+:32];assign qs[b*6+:6]=shift[quant_channel*6+:6];assign qz[b*8+:8]=yzero[quant_channel*8+:8];
 end endgenerate
 fused_affine_quant_drop_pipe #(.LANES(16)) quant(
  .aclk(aclk),.aresetn(aresetn && state==QUANT),
  .s_acc(qa),.s_bias(qb),.s_multiplier(qm),.s_shift(qs),.s_zero(qz),
  .s_valid(state==QUANT && quant_sent<quant_chunks),.s_ready(qready),
  .m_data(qdata),.m_valid(qvalid),.m_ready(state==QUANT && !quant_done),.m_error(qerror));
 integer i;
 wire [LANES*16-1:0] repeated_bank_data={bank_data,bank_data};
 wire [LANES*8-1:0] rotated_bank_data=repeated_bank_data >> {read_rotation,3'b0};
 reg [LANES*8-1:0] aligned_data;
 reg [LANES-1:0] aligned_inside;
 reg [31:0] aligned_weight;
 reg align_valid,aligned_last;
 reg signed [8:0] centered;
 reg signed [17:0] position_i;
 always @(posedge aclk) begin
  if(!aresetn)begin
   state<=IDLE;times<=0;valid_times<=0;kernel<=0;tap<=0;read_tap<=0;
   weights<=0;bias<=0;multiplier<=0;shift<=0;xzero<=0;yzero<=0;short_mode<=0;
   tiles<=0;write_tile<=0;time_tile<=0;read_valid<=0;product_valid<=0;
   aligned_data<=0;aligned_inside<=0;aligned_weight<=0;align_valid<=0;aligned_last<=0;
   product_last<=0;quant_sent<=0;quant_received<=0;quant_done<=0;quant_buffer<=0;read_rotation<=0;read_inside<=0;error<=0;done<=0;
   for(i=0;i<LANES;i=i+1)begin accum[i]<=0;products[i]<=0;end
  end else begin
   done<=0;read_valid<=issue;
   align_valid<=state==MAC && read_valid;
   product_valid<=state==MAC && align_valid;
   if(issue)begin
    read_tap<=tap;read_rotation<=rotation;tap<=tap+1;
    for(i=0;i<LANES;i=i+1)begin
     position_i=base+(quad_mode?(i%16):dual_mode?(i%32):i);
     read_inside[i]<=position_i>=0 && position_i<times;
    end
   end
   if(state==MAC && read_valid)begin
    aligned_data<=rotated_bank_data;aligned_inside<=read_inside;
    for(i=0;i<4;i=i+1)aligned_weight[i*8+:8]<=weights[(short_mode?i:0)*MAX_KERNEL*8+read_tap*8+:8];
    aligned_last<=read_tap==kernel-1;
   end
   if(state==MAC && align_valid)begin
    product_last<=aligned_last;
    for(i=0;i<LANES;i=i+1)begin
     centered=aligned_inside[i]?$signed({1'b0,aligned_data[i*8+:8]})-$signed({1'b0,xzero[(quad_mode?(i/16):dual_mode?(i/32):0)*8+:8]}):9'sd0;
     products[i]<=centered*$signed(aligned_weight[(quad_mode?(i/16):dual_mode?(i/32):0)*8+:8]);
    end
   end
   if(state==MAC && product_valid)
    for(i=0;i<LANES;i=i+1)accum[i]<=accum[i]+products[i];
   case(state)
    IDLE:if(cfg_valid)begin
     error<=0;
     if(cfg_times==0 || cfg_times>MAX_T || cfg_valid_times>cfg_times ||
        cfg_kernel==0 || cfg_kernel>MAX_KERNEL || !cfg_kernel[0] ||
        |bad_config)begin error<=1;done<=1;end
     else begin
      times<=cfg_times;valid_times<=cfg_valid_times;kernel<=cfg_kernel;weights<=cfg_weights;
      bias<=cfg_bias;multiplier<=cfg_multiplier;shift<=cfg_shift;xzero<=cfg_xzero;yzero<=cfg_yzero;short_mode<=cfg_times<=32;
      tiles<=(cfg_times+LANES-1)>>LW;write_tile<=0;time_tile<=0;state<=INPUTS;
     end
    end
    INPUTS:if(s_valid)begin
     if(write_tile==tiles-1)state<=CLEAR;else write_tile<=write_tile+1;
    end
    CLEAR:begin
     for(i=0;i<LANES;i=i+1)accum[i]<=0;
     tap<=0;read_valid<=0;align_valid<=0;product_valid<=0;quant_sent<=0;quant_received<=0;quant_done<=0;quant_buffer<=0;state<=MAC;
    end
    MAC:if(product_valid && product_last)state<=QUANT;
    QUANT:begin
     if(quant_sent<quant_chunks && qready)quant_sent<=quant_sent+1;
     if(qvalid && !quant_done)begin
      quant_buffer[quant_received[1:0]*128+:128]<=qdata;quant_received<=quant_received+1;
      if(qerror)error<=1;
      if(quant_received==quant_chunks-1)quant_done<=1;
     end
     if(quant_done && m_ready)begin
      if(time_tile==tiles-1)begin state<=IDLE;done<=1;end
      else begin time_tile<=time_tile+1;state<=CLEAR;end
     end
    end
    default:begin state<=IDLE;error<=1;done<=1;end
   endcase
  end
 end
endmodule
