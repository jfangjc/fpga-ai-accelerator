`timescale 1ns/1ps
module tb_ab_mac_conformance;
 reg aclk=0;always #5 aclk=~aclk;
 reg [31:0] lfsr=32'hd12a0937;reg [7:0] av=0,bv=0;
 reg cem=0,cep=0,rm=1,rp=1;wire [47:0] old_value,new_value,model_value;
 integer cycle=0,checks=0,index;reg overflow_seen=0;
   DSP48E2 #(.AREG(0),.ACASCREG(0),.BREG(0),.BCASCREG(0),.ADREG(0),.DREG(0),.CREG(0),
    .MREG(1),.PREG(1),.ALUMODEREG(0),.OPMODEREG(0),.INMODEREG(0),.CARRYINREG(0),.CARRYINSELREG(0),.USE_MULT("MULTIPLY")) old_mac(
    .CLK(aclk),.A({{22{av[7]}},av}),.B({{10{bv[7]}},bv}),
    .C(48'b0),.D(27'b0),.ACIN(30'b0),.BCIN(18'b0),.PCIN(48'b0),.ALUMODE(4'b0),.INMODE(5'b0),.OPMODE(9'b000100101),
    .CARRYIN(1'b0),.CARRYINSEL(3'b0),.CARRYCASCIN(1'b0),.MULTSIGNIN(1'b0),.CEA1(1'b0),.CEA2(1'b0),.CEB1(1'b0),.CEB2(1'b0),
    .CEAD(1'b0),.CEC(1'b0),.CED(1'b0),.CEALUMODE(1'b0),.CECTRL(1'b0),.CEINMODE(1'b0),.CECARRYIN(1'b0),
    .CEM(cem),.CEP(cep),.RSTA(1'b0),.RSTB(1'b0),.RSTC(1'b0),.RSTD(1'b0),
    .RSTALUMODE(1'b0),.RSTCTRL(1'b0),.RSTINMODE(1'b0),.RSTALLCARRYIN(1'b0),.RSTM(rm),
    .RSTP(rp),.P(old_value),.ACOUT(),.BCOUT(),.PCOUT(),
    .CARRYCASCOUT(),.CARRYOUT(),.MULTSIGNOUT(),.OVERFLOW(),.UNDERFLOW(),.PATTERNDETECT(),.PATTERNBDETECT(),.XOROUT());
   DSP48E2 #(.AREG(1),.ACASCREG(1),.BREG(1),.BCASCREG(1),.ADREG(0),.DREG(0),.CREG(0),
    .MREG(0),.PREG(1),.ALUMODEREG(0),.OPMODEREG(0),.INMODEREG(0),.CARRYINREG(0),.CARRYINSELREG(0),.USE_MULT("MULTIPLY")) new_mac(
    .CLK(aclk),.A({{22{av[7]}},av}),.B({{10{bv[7]}},bv}),
    .C(48'b0),.D(27'b0),.ACIN(30'b0),.BCIN(18'b0),.PCIN(48'b0),.ALUMODE(4'b0),.INMODE(5'b0),.OPMODE(9'b000100101),
    .CARRYIN(1'b0),.CARRYINSEL(3'b0),.CARRYCASCIN(1'b0),.MULTSIGNIN(1'b0),.CEA1(1'b0),.CEA2(cem),.CEB1(1'b0),.CEB2(cem),
    .CEAD(1'b0),.CEC(1'b0),.CED(1'b0),.CEALUMODE(1'b0),.CECTRL(1'b0),.CEINMODE(1'b0),.CECARRYIN(1'b0),
    .CEM(cem),.CEP(cep),.RSTA(rm),.RSTB(rm),.RSTC(1'b0),.RSTD(1'b0),
    .RSTALUMODE(1'b0),.RSTCTRL(1'b0),.RSTINMODE(1'b0),.RSTALLCARRYIN(1'b0),.RSTM(rm),
    .RSTP(rp),.P(new_value),.ACOUT(),.BCOUT(),.PCOUT(),
    .CARRYCASCOUT(),.CARRYOUT(),.MULTSIGNOUT(),.OVERFLOW(),.UNDERFLOW(),.PATTERNDETECT(),.PATTERNBDETECT(),.XOROUT());
   qn_mac_ab_model #(.AREG(1),.ACASCREG(1),.BREG(1),.BCASCREG(1),.ADREG(0),.DREG(0),.CREG(0),
    .MREG(0),.PREG(1),.ALUMODEREG(0),.OPMODEREG(0),.INMODEREG(0),.CARRYINREG(0),.CARRYINSELREG(0),.USE_MULT("MULTIPLY")) model_mac(
    .CLK(aclk),.A({{22{av[7]}},av}),.B({{10{bv[7]}},bv}),
    .C(48'b0),.D(27'b0),.ACIN(30'b0),.BCIN(18'b0),.PCIN(48'b0),.ALUMODE(4'b0),.INMODE(5'b0),.OPMODE(9'b000100101),
    .CARRYIN(1'b0),.CARRYINSEL(3'b0),.CARRYCASCIN(1'b0),.MULTSIGNIN(1'b0),.CEA1(1'b0),.CEA2(cem),.CEB1(1'b0),.CEB2(cem),
    .CEAD(1'b0),.CEC(1'b0),.CED(1'b0),.CEALUMODE(1'b0),.CECTRL(1'b0),.CEINMODE(1'b0),.CECARRYIN(1'b0),
    .CEM(cem),.CEP(cep),.RSTA(rm),.RSTB(rm),.RSTC(1'b0),.RSTD(1'b0),
    .RSTALUMODE(1'b0),.RSTCTRL(1'b0),.RSTINMODE(1'b0),.RSTALLCARRYIN(1'b0),.RSTM(rm),
    .RSTP(rp),.P(model_value),.ACOUT(),.BCOUT(),.PCOUT(),
    .CARRYCASCOUT(),.CARRYOUT(),.MULTSIGNOUT(),.OVERFLOW(),.UNDERFLOW(),.PATTERNDETECT(),.PATTERNBDETECT(),.XOROUT());

 always @(negedge aclk)begin
  cycle=cycle+1;
  if(cycle>20)begin
   if(old_value!==new_value || new_value!==model_value)
    $fatal(1,"MAC retiming mismatch cycle %0d old=%h new=%h model=%h",cycle,old_value,new_value,model_value);
   checks=checks+1;
  end
  lfsr={lfsr[30:0],lfsr[31]^lfsr[21]^lfsr[1]^lfsr[0]};
  if(cycle<=20)begin rm=1;rp=1;cem=0;cep=0;av=0;bv=0;end
  else if(cycle<=65556)begin
   index=cycle-21;av=index[7:0];bv=index[15:8];cem=1;cep=1;rm=0;rp=0;
  end else if(cycle<=65558)begin cem=0;cep=1;rm=0;rp=0;end
  else if(cycle<=71558)begin
   av=lfsr[7:0];bv=lfsr[23:16];cem=lfsr[9];cep=lfsr[15];
   rm=cycle%67==0 || cycle%109==0;rp=cycle%83==0 || cycle%109==0;
  end else if(cycle<=71562)begin rm=1;rp=1;cem=0;cep=0;av=0;bv=0;end
  else begin
   av=8'h80;bv=8'h80;cem=1;cep=1;rm=0;rp=0;
   if(old_value[31] && !old_value[47])overflow_seen=1;
  end
  if(cycle==204000)begin
   if(!overflow_seen)$fatal(1,"INT32 accumulation boundary was not crossed");
   $display("PASS: %0d old/new vendor DSP and model checks, all 65536 signed byte products, independent enables/resets and INT32 overflow",checks);
   $finish;
  end
 end
endmodule
