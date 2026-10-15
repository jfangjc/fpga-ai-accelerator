`timescale 1ns/1ps
// Exact ARM float32 requantization using explicit 24-bit significands.
// A valid multiplier has seven zero low bits. INT32 -> float32 and multiply
// each round to nearest-even. Magnitudes >=256 can be capped before UINT8
// saturation for either sign and any UINT8 zero point. No precision is lost.
module fused_affine_quant_drop_pipe #(parameter LANES=16)(
 input wire aclk,aresetn,
 input wire [LANES*32-1:0] s_acc,s_bias,s_multiplier,
 input wire [LANES*6-1:0] s_shift,input wire [LANES*8-1:0] s_zero,
 input wire s_valid,output wire s_ready,
 output reg [LANES*8-1:0] m_data,output wire m_valid,input wire m_ready,output reg m_error
);
 reg [7:0] valid;wire [7:0] ready;
 assign ready[7]=!valid[7] || m_ready;
 genvar g;
 generate for(g=0;g<7;g=g+1)begin: elastic
  assign ready[g]=!valid[g] || ready[g+1];
 end endgenerate
 assign s_ready=aresetn && ready[0];assign m_valid=aresetn && valid[7];
 reg [31:0] magnitude[0:LANES-1];
 reg [23:0] multipliers[0:1][0:LANES-1],a_mant[0:LANES-1],p_mant[0:LANES-1];
 reg [3:0] a_drop[1:4][0:LANES-1];
 reg [47:0] product[0:LANES-1],product_hold[0:LANES-1];
 reg [5:0] p_drop[0:LANES-1],p_drop_rounded[0:LANES-1];
 reg signed [9:0] integer_result[0:LANES-1];
 reg [5:0] shifts[0:6][0:LANES-1];reg [7:0] zeros[0:6][0:LANES-1];
 reg [LANES-1:0] negative[0:6],invalid[0:6];
 function automatic [2:0] highest8(input [7:0] v);
  begin
   casez(v)
    8'b1???????:highest8=7;8'b01??????:highest8=6;8'b001?????:highest8=5;
    8'b0001????:highest8=4;8'b00001???:highest8=3;8'b000001??:highest8=2;
    8'b0000001?:highest8=1;default:highest8=0;
   endcase
  end
 endfunction
 function automatic [27:0] normalize_integer(input [31:0] value);
  reg [3:0] drop;reg [24:0] quotient;reg [31:0] remainder,half;
  begin
   drop=|value[31:24]?{1'b0,highest8(value[31:24])}+1:0;
   quotient=value>>drop;remainder=value&((32'd1<<drop)-1);
   half=32'd1<<(drop?drop-1:0);
   if(drop && (remainder>half || (remainder==half && quotient[0])))quotient=quotient+1;
   if(quotient[24])begin quotient=25'h0800000;drop=drop+1;end
   normalize_integer={drop,quotient[23:0]};
  end
 endfunction
 function automatic [5:0] product_drop(input [47:0] value);
  begin
   if(|value[47:40])product_drop={3'b0,highest8(value[47:40])}+17;
   else if(|value[39:32])product_drop={3'b0,highest8(value[39:32])}+9;
   else if(|value[31:24])product_drop={3'b0,highest8(value[31:24])}+1;
   else product_drop=0;
  end
 endfunction
 function automatic [29:0] normalize_product(input [47:0] value,input [5:0] initial_drop);
  reg [5:0] drop;reg [24:0] quotient;reg [23:0] remainder,half,mask;
  begin
   drop=initial_drop;quotient=value>>drop;
   mask=(24'd1<<drop)-1;remainder=value[23:0]&mask;half=24'd1<<(drop?drop-1:0);
   if(drop && (remainder>half || (remainder==half && quotient[0])))quotient=quotient+1;
   if(quotient[24])begin quotient=25'h0800000;drop=drop+1;end
   normalize_product={drop,quotient[23:0]};
  end
 endfunction
 function automatic signed [9:0] final_integer(input [23:0] mant,input signed [7:0] drop,input neg,input [7:0] zero);
  reg [24:0] quotient;reg [23:0] remainder,half,mask;
  reg [8:0] mag;reg [15:0] expanded;reg [7:0] left_shift;
  begin
   mag=0;quotient=0;remainder=0;half=0;mask=0;expanded=0;left_shift=0;
   if(mant==0 || drop>24)mag=0;
   else if(drop>0)begin
    quotient=mant>>drop[4:0];mask=(24'd1<<drop[4:0])-1;
    remainder=mant&mask;half=24'd1<<(drop[4:0]-1);
    if(remainder>half || (remainder==half && quotient[0]))quotient=quotient+1;
    mag=quotient>=256?9'd256:quotient[8:0];
   end else if(drop<=-8 || mant>=256)mag=256;
   else begin
    left_shift=-drop;expanded={8'b0,mant[7:0]}<<left_shift[2:0];
    mag=expanded>=256?9'd256:expanded[8:0];
   end
   final_integer=neg?$signed({2'b0,zero})-$signed({1'b0,mag}):
                     $signed({2'b0,zero})+$signed({1'b0,mag});
  end
 endfunction
 // Separate the shift arithmetic from the barrel shift / rounding / zero add.
 reg signed [7:0] round_drop[0:LANES-1];reg [23:0] round_mant[0:LANES-1];
 integer i,j;reg signed [32:0] added;reg [27:0] normalized_a;reg [29:0] normalized_p;
 reg signed [7:0] effective_drop;
 always @(posedge aclk)begin
  if(!aresetn)begin
   valid<=0;m_data<=0;m_error<=0;
   for(j=0;j<7;j=j+1)begin negative[j]<=0;invalid[j]<=0;end
  end else begin
   if(ready[7])begin
    valid[7]<=valid[6];
    if(valid[6])begin
     m_error<=|invalid[6];
     for(i=0;i<LANES;i=i+1)
      m_data[i*8+:8]<=invalid[6][i] || integer_result[i]<0?0:
                       integer_result[i]>255?255:integer_result[i][7:0];
    end
   end
   for(j=1;j<7;j=j+1)if(ready[j])begin
    valid[j]<=valid[j-1];
    if(valid[j-1])begin
     negative[j]<=negative[j-1];invalid[j]<=invalid[j-1];
     for(i=0;i<LANES;i=i+1)begin shifts[j][i]<=shifts[j-1][i];zeros[j][i]<=zeros[j-1][i];end
    end
   end
   if(ready[6] && valid[5])for(i=0;i<LANES;i=i+1)begin
    integer_result[i]<=final_integer(round_mant[i],round_drop[i],negative[5][i],zeros[5][i]);
   end
   if(ready[5] && valid[4])for(i=0;i<LANES;i=i+1)begin
    effective_drop=$signed({2'b0,shifts[4][i]})-$signed({4'b0,a_drop[4][i]})-8'sd7-$signed({2'b0,p_drop_rounded[i]});
    round_drop[i]<=effective_drop;round_mant[i]<=p_mant[i];
   end
   if(ready[4] && valid[3])for(i=0;i<LANES;i=i+1)begin
    normalized_p=normalize_product(product_hold[i],p_drop[i]);
    p_mant[i]<=normalized_p[23:0];p_drop_rounded[i]<=normalized_p[29:24];a_drop[4][i]<=a_drop[3][i];
   end
   if(ready[3] && valid[2])for(i=0;i<LANES;i=i+1)begin
    product_hold[i]<=product[i];p_drop[i]<=product_drop(product[i]);a_drop[3][i]<=a_drop[2][i];
   end
   if(ready[2] && valid[1])for(i=0;i<LANES;i=i+1)begin
    product[i]<=a_mant[i]*multipliers[1][i];a_drop[2][i]<=a_drop[1][i];
   end
   if(ready[1] && valid[0])for(i=0;i<LANES;i=i+1)begin
    normalized_a=normalize_integer(magnitude[i]);a_mant[i]<=normalized_a[23:0];a_drop[1][i]<=normalized_a[27:24];
    multipliers[1][i]<=multipliers[0][i];
   end
   if(ready[0])begin
    valid[0]<=s_valid;
    if(s_valid)for(i=0;i<LANES;i=i+1)begin
     added=$signed({s_acc[i*32+31],s_acc[i*32+:32]})+$signed({s_bias[i*32+31],s_bias[i*32+:32]});
     magnitude[i]<=added[31]?-$signed(added[31:0]):added[31:0];negative[0][i]<=added[31];
     multipliers[0][i]<=s_multiplier[i*32+7+:24];shifts[0][i]<=s_shift[i*6+:6];zeros[0][i]<=s_zero[i*8+:8];
     invalid[0][i]<=added[32]!=added[31] || s_multiplier[i*32+31] || |s_multiplier[i*32+:7] ||
                    s_shift[i*6+:6]==0 || s_shift[i*6+:6]==63;
    end
   end
  end
 end
endmodule
