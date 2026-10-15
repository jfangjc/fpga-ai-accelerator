// QNC2 cached weights, paired output groups (512 MACs), retained full input.
// RUN_BULK=6: word2={valid_times,K}; channel-major padded UINT8 input.
// Response: tagged ACK, tile/group/time/channel UINT8 outputs, 16-byte counters.
module qn_branch512_ab_pw_core #(parameter MAX_K=256,parameter MAX_M=256)(
 (* X_INTERFACE_PARAMETER="ASSOCIATED_BUSIF S_AXIS:M_AXIS, ASSOCIATED_RESET aresetn" *)
 (* X_INTERFACE_INFO="xilinx.com:signal:clock:1.0 aclk CLK" *) input wire aclk,
 (* X_INTERFACE_PARAMETER="POLARITY ACTIVE_LOW" *)
 (* X_INTERFACE_INFO="xilinx.com:signal:reset:1.0 aresetn RST" *) input wire aresetn,
 (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TDATA" *) input wire [127:0] s_axis_tdata,
 (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TVALID" *) input wire s_axis_tvalid,
 (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TREADY" *) output wire s_axis_tready,
 (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TLAST" *) input wire s_axis_tlast,
 (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 M_AXIS TDATA" *) output wire [127:0] m_axis_tdata,
 (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 M_AXIS TVALID" *) output wire m_axis_tvalid,
 (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 M_AXIS TREADY" *) input wire m_axis_tready,
 (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 M_AXIS TLAST" *) output wire m_axis_tlast,
 input wire allow_wrap,input wire [4:0] cache_select, output wire busy,output reg done,output reg error);
 localparam KW=$clog2(MAX_K),GW=$clog2((MAX_M+15)/16);
 localparam HEADER=0,WEIGHTS=1,PARAMS=2,INPUTS=3,CLEAR=4,COMPUTE=5,
            RESPONSE=6,QUANT=7,RAW=8,DRAIN=9,TRAILER=10,QCLEAR=11;
 integer reset_slot;
 reg [3:0] state;
 reg [7:0] opcode;
 reg [7:0] slot_valid,slot_quant;
 reg [15:0] slot_k[0:7],slot_m[0:7];
 reg [31:0] slot_tag[0:7];
 wire cache_valid=slot_valid[cache_select],cache_quant=slot_quant[cache_select];
 wire [15:0] cache_k=slot_k[cache_select],cache_m=slot_m[cache_select];
 wire [31:0] cache_tag=slot_tag[cache_select];
 reg is_quant,is_bulk,payload,header_sent,upper_group;
 reg [15:0] request_k,request_m,request_t;
 reg [31:0] tag,status;
 reg [KW:0] issued;
 reg [KW-1:0] load_k;
 reg [GW-1:0] load_group,compute_group;
 reg [GW:0] groups;
 reg [12:0] tiles,time_tile;
 reg [16:0] input_beats,write_beat,read_address;
 reg [3:0] param_lane;
 reg [5:0] output_beat;
 reg [4:0] quant_feed;
 reg read_stage,read_stage_last,read_valid,read_last,product_valid,product_last;
 reg [31:0] mac_cycles,input_stalls,output_stalls,active_cycles;
 reg [127:0] counter_snapshot;
 wire accept=s_axis_tvalid && s_axis_tready;
 wire issue=state==COMPUTE && issued<request_k;
 wire [15:0] hk=s_axis_tdata[79:64],hm=s_axis_tdata[63:48],ht=s_axis_tdata[95:80];
 wire [12:0] htiles=({1'b0,ht}+15)>>4;
 wire [28:0] hbeats=hk*htiles;
 wire [7:0] hop=s_axis_tdata[39:32];
 wire [GW-1:0] selected_group=compute_group+upper_group;
 wire last_group=selected_group==groups-1;
 wire last_time=time_tile==tiles-1;
 wire pair_has_upper=compute_group+1<groups;
 wire [127:0] input_row,weight_low,weight_high_read;
 wire [255:0] weights={pair_has_upper?weight_high_read:128'b0,weight_low};
 wire [KW+GW+2:0] weight_a_addr=state==WEIGHTS ? {cache_select[2:0],load_group,load_k} : {cache_select[2:0],compute_group,issued[KW-1:0]};
 wire [GW-1:0] high_group=pair_has_upper ? compute_group+1 : compute_group;
 wire [KW+GW+2:0] weight_b_addr={cache_select[2:0],high_group,issued[KW-1:0]};
 wire signed [31:0] accum[0:511];
 wire [511:0] qa,qb,qm;
 wire [95:0] qs;
 wire [127:0] qz,qdata;
 wire qready,qvalid,qerror;
 wire qreset=aresetn && state==QUANT && !error;
 genvar lane;
 generate for(lane=0;lane<16;lane=lane+1) begin: params_bank
  (* ram_style="block" *) reg [77:0] params[0:8*((MAX_M+15)/16)-1];
  always @(posedge aclk) if(accept && state==PARAMS && param_lane==lane)
   params[{cache_select[2:0],load_group}]<={s_axis_tdata[79:72],s_axis_tdata[69:64],s_axis_tdata[63:0]};
  reg [77:0] current_params;
  always @(posedge aclk) current_params<=params[{cache_select[2:0],selected_group}];
  assign qb[lane*32+:32]=current_params[31:0];
  assign qm[lane*32+:32]=current_params[63:32];
  assign qs[lane*6+:6]=current_params[69:64];
  assign qz[lane*8+:8]=current_params[77:70];
  assign qa[lane*32+:32]=accum[quant_feed[3:0]*32+upper_group*16+lane];
 end endgenerate
 // Elastic input register splits the accumulation selector from quantization.
 reg [511:0] buffer_acc,buffer_bias,buffer_multiplier;
 reg [95:0] buffer_shift;reg [127:0] buffer_zero;
 reg buffer_valid,buffer_wrap;wire quant_ready;
 assign qready=!buffer_valid || quant_ready;
 always @(posedge aclk)begin
  if(!qreset)begin
   buffer_valid<=0;buffer_wrap<=0;buffer_acc<=0;buffer_bias<=0;
   buffer_multiplier<=0;buffer_shift<=0;buffer_zero<=0;
  end else if(qready)begin
   buffer_valid<=quant_feed<16;
   if(quant_feed<16)begin
    buffer_acc<=qa;buffer_bias<=qb;buffer_multiplier<=qm;
    buffer_shift<=qs;buffer_zero<=qz;buffer_wrap<=allow_wrap;
   end
  end
 end
 fused_affine_quant_select_wrap #(.LANES(16)) requant(.aclk(aclk),.aresetn(qreset),
  .s_acc(buffer_acc),.s_bias(buffer_bias),.s_multiplier(buffer_multiplier),.s_shift(buffer_shift),.s_zero(buffer_zero),
  .s_valid(buffer_valid),.s_ready(quant_ready),
  .m_data(qdata),.m_valid(qvalid),.m_ready(state==QUANT && m_axis_tready && !error),.m_error(qerror),.s_wrap(buffer_wrap));
 assign s_axis_tready=aresetn && (state==HEADER || state==WEIGHTS || state==PARAMS || state==INPUTS || state==DRAIN);
 assign m_axis_tvalid=aresetn && (state==RESPONSE || state==TRAILER || state==RAW || (state==QUANT && (error || qvalid)));
 assign m_axis_tlast=state==RESPONSE ? !payload : state==TRAILER ||
   (!is_bulk && last_group && output_beat==(is_quant?15:63));
 wire [8:0] raw_index={output_beat[5:2],5'b0}+upper_group*16+output_beat[1:0]*4;
 assign m_axis_tdata=state==RESPONSE ? {tag,request_m,request_k,status,32'h32524e51} :
  state==TRAILER ? counter_snapshot : error ? 128'b0 :
  state==QUANT ? qdata : {accum[raw_index+3],accum[raw_index+2],accum[raw_index+1],accum[raw_index]};
 assign busy=state!=HEADER;
 xpm_memory_sdpram #(.MEMORY_SIZE(4096*128),.MEMORY_PRIMITIVE("block"),
  .CLOCKING_MODE("common_clock"),.ECC_MODE("no_ecc"),.USE_MEM_INIT(0),
  .WRITE_DATA_WIDTH_A(128),.BYTE_WRITE_WIDTH_A(128),.ADDR_WIDTH_A(12),
  .READ_DATA_WIDTH_B(128),.ADDR_WIDTH_B(12),.READ_LATENCY_B(2),.WRITE_MODE_B("read_first"),.SIM_ASSERT_CHK(1)) input_memory(
  .clka(aclk),.clkb(aclk),.sleep(1'b0),.ena(accept && state==INPUTS),.wea(accept && state==INPUTS),
  .addra(write_beat[11:0]),.dina(s_axis_tdata),.injectsbiterra(1'b0),.injectdbiterra(1'b0),
  .rstb(1'b0),.enb(issue),.regceb(1'b1),.addrb(read_address[11:0]),.doutb(input_row),.sbiterrb(),.dbiterrb());
 xpm_memory_tdpram #(.MEMORY_SIZE((1<<(KW+GW+3))*128),.MEMORY_PRIMITIVE("ultra"),
  .CLOCKING_MODE("common_clock"),.ECC_MODE("no_ecc"),.USE_MEM_INIT(0),
  .WRITE_DATA_WIDTH_A(128),.WRITE_DATA_WIDTH_B(128),.BYTE_WRITE_WIDTH_A(128),.BYTE_WRITE_WIDTH_B(128),
  .ADDR_WIDTH_A(KW+GW+3),.ADDR_WIDTH_B(KW+GW+3),.READ_DATA_WIDTH_A(128),.READ_DATA_WIDTH_B(128),
  .READ_LATENCY_A(2),.READ_LATENCY_B(2),.WRITE_MODE_A("no_change"),.WRITE_MODE_B("no_change"),.SIM_ASSERT_CHK(1)) weight_memory(
  .clka(aclk),.clkb(aclk),.sleep(1'b0),.ena(issue || (accept && state==WEIGHTS)),.wea(accept && state==WEIGHTS),
  .addra(weight_a_addr),.dina(s_axis_tdata),.injectsbiterra(1'b0),.injectdbiterra(1'b0),.rsta(1'b0),.regcea(1'b1),
  .enb(issue),.web(1'b0),.addrb(weight_b_addr),.dinb(128'b0),.injectsbiterrb(1'b0),.injectdbiterrb(1'b0),
  .rstb(1'b0),.regceb(1'b1),.douta(weight_low),.doutb(weight_high_read),.sbiterra(),.dbiterra(),.sbiterrb(),.dbiterrb());
 genvar t,o;
 generate for(t=0;t<16;t=t+1) begin: times
  wire [7:0] byte_value=input_row[t*8+:8] ^ (is_bulk?8'h80:8'h00);
  for(o=0;o<32;o=o+1) begin: outputs
   wire [47:0] sum;assign accum[t*32+o]=sum[31:0];
   DSP48E2 #(.AREG(1),.ACASCREG(1),.BREG(1),.BCASCREG(1),.ADREG(0),.DREG(0),.CREG(0),
    .MREG(0),.PREG(1),.ALUMODEREG(0),.OPMODEREG(0),.INMODEREG(0),.CARRYINREG(0),.CARRYINSELREG(0),.USE_MULT("MULTIPLY")) mac(
    .CLK(aclk),.A({{22{byte_value[7]}},byte_value}),.B({{10{weights[o*8+7]}},weights[o*8+:8]}),
    .C(48'b0),.D(27'b0),.ACIN(30'b0),.BCIN(18'b0),.PCIN(48'b0),.ALUMODE(4'b0),.INMODE(5'b0),.OPMODE(9'b000100101),
    .CARRYIN(1'b0),.CARRYINSEL(3'b0),.CARRYCASCIN(1'b0),.MULTSIGNIN(1'b0),.CEA1(1'b0),.CEA2(read_valid),.CEB1(1'b0),.CEB2(read_valid),
    .CEAD(1'b0),.CEC(1'b0),.CED(1'b0),.CEALUMODE(1'b0),.CECTRL(1'b0),.CEINMODE(1'b0),.CECARRYIN(1'b0),
    .CEM(read_valid),.CEP(state==COMPUTE && product_valid),.RSTA(!aresetn),.RSTB(!aresetn),.RSTC(1'b0),.RSTD(1'b0),
    .RSTALUMODE(1'b0),.RSTCTRL(1'b0),.RSTINMODE(1'b0),.RSTALLCARRYIN(1'b0),.RSTM(!aresetn),
    .RSTP(!aresetn || state==CLEAR || (accept && state==HEADER)),.P(sum),.ACOUT(),.BCOUT(),.PCOUT(),
    .CARRYCASCOUT(),.CARRYOUT(),.MULTSIGNOUT(),.OVERFLOW(),.UNDERFLOW(),.PATTERNDETECT(),.PATTERNBDETECT(),.XOROUT());
  end
 end endgenerate
 always @(posedge aclk) begin
  if(!aresetn) begin
   state<=HEADER;slot_valid<=0;slot_quant<=0;error<=0;done<=0;opcode<=0;
   request_k<=0;request_m<=0;request_t<=0;tag<=0;for(reset_slot=0;reset_slot<8;reset_slot=reset_slot+1)begin slot_k[reset_slot]<=0;slot_m[reset_slot]<=0;slot_tag[reset_slot]<=0;end status<=0;
   issued<=0;load_k<=0;load_group<=0;compute_group<=0;groups<=1;tiles<=1;time_tile<=0;
   input_beats<=0;write_beat<=0;read_address<=0;param_lane<=0;output_beat<=0;quant_feed<=0;
   is_quant<=0;is_bulk<=0;payload<=0;header_sent<=0;upper_group<=0;
   read_stage<=0;read_stage_last<=0;read_valid<=0;read_last<=0;product_valid<=0;product_last<=0;
   mac_cycles<=0;input_stalls<=0;output_stalls<=0;active_cycles<=0;counter_snapshot<=0;
  end else begin
   done<=0;read_stage<=issue;read_stage_last<=issue && issued==request_k-1;
   read_valid<=read_stage;read_last<=read_stage_last;product_valid<=read_valid;product_last<=read_last;
   if(state!=HEADER)active_cycles<=active_cycles+1;
   if(issue)mac_cycles<=mac_cycles+1;
   if(state==INPUTS && !s_axis_tvalid)input_stalls<=input_stalls+1;
   if(m_axis_tvalid && !m_axis_tready)output_stalls<=output_stalls+1;
   case(state)
    HEADER: if(accept) begin
     error<=0;status<=0;opcode<=hop;request_k<=hk;request_m<=hm;request_t<=ht;tag<=s_axis_tdata[127:96];
     groups<=(hm+15)>>4;tiles<=hop==6?htiles:1;input_beats<=hop==6?hbeats:hk;
     is_bulk<=hop==6;is_quant<=hop==5 || hop==6;payload<=hop==2 || hop==5 || hop==6;
     header_sent<=0;upper_group<=0;compute_group<=0;load_group<=0;load_k<=0;write_beat<=0;
     time_tile<=0;param_lane<=0;output_beat<=0;quant_feed<=0;issued<=0;
     mac_cycles<=0;input_stalls<=0;output_stalls<=0;active_cycles<=0;
     if(s_axis_tdata[31:0]!=32'h32434e51 || s_axis_tdata[47:40]!=0 || cache_select>=8 ||
        (hop!=6 && ht!=0) || hk>MAX_K || hm>MAX_M ||
        (hop==6 && (ht==0 || ht>256 || hbeats==0 || hbeats>4096))) begin
      error<=1;slot_valid[cache_select]<=0;payload<=0;status<=1;state<=s_axis_tlast?RESPONSE:DRAIN;
     end else if(hop==3 && hk==0 && hm==0 && s_axis_tdata[127:96]==0) begin
      slot_valid[cache_select]<=0;payload<=0;if(s_axis_tlast)state<=RESPONSE;else begin error<=1;status<=2;state<=DRAIN;end
     end else if(hk==0 || hm==0 || s_axis_tdata[127:96]==0 || (hop!=1 && hop!=2 && hop!=4 && hop!=5 && hop!=6)) begin
      error<=1;slot_valid[cache_select]<=0;payload<=0;status<=1;state<=s_axis_tlast?RESPONSE:DRAIN;
     end else if(s_axis_tlast) begin error<=1;slot_valid[cache_select]<=0;payload<=0;status<=2;state<=RESPONSE;end
     else if(hop==1 || hop==4) begin slot_valid[cache_select]<=0;state<=WEIGHTS;end
     else if(!cache_valid || cache_k!=hk || cache_m!=hm || cache_tag!=s_axis_tdata[127:96] || cache_quant!=(hop==5 || hop==6)) begin
      error<=1;slot_valid[cache_select]<=0;payload<=0;status<=3;state<=DRAIN;
     end else state<=INPUTS;
    end
    WEIGHTS: if(accept) begin
     if(load_k==request_k-1 && load_group==groups-1) begin
      if(opcode==4 && !s_axis_tlast)begin load_group<=0;param_lane<=0;state<=PARAMS;end
      else if(opcode==1 && s_axis_tlast)begin slot_valid[cache_select]<=1;slot_quant[cache_select]<=0;slot_k[cache_select]<=request_k;slot_m[cache_select]<=request_m;slot_tag[cache_select]<=tag;state<=RESPONSE;end
      else begin error<=1;status<=2;state<=s_axis_tlast?RESPONSE:DRAIN;end
     end else if(s_axis_tlast)begin error<=1;status<=2;state<=RESPONSE;end
     else if(load_k==request_k-1)begin load_k<=0;load_group<=load_group+1;end else load_k<=load_k+1;
    end
    PARAMS: if(accept)begin
     if(s_axis_tdata[127:80]!=0 || s_axis_tdata[71:70]!=0 || s_axis_tdata[63] || |s_axis_tdata[38:32] || s_axis_tdata[69:64]==0 || s_axis_tdata[69:64]==63)begin
      error<=1;status<=1;state<=s_axis_tlast?RESPONSE:DRAIN;
     end else if(param_lane==15 && load_group==groups-1)begin
      if(!s_axis_tlast)begin error<=1;status<=2;state<=DRAIN;end
      else begin slot_valid[cache_select]<=1;slot_quant[cache_select]<=1;slot_k[cache_select]<=request_k;slot_m[cache_select]<=request_m;slot_tag[cache_select]<=tag;state<=RESPONSE;end
     end else if(s_axis_tlast)begin error<=1;status<=2;state<=RESPONSE;end
     else if(param_lane==15)begin param_lane<=0;load_group<=load_group+1;end else param_lane<=param_lane+1;
    end
    INPUTS: if(accept)begin
     if(write_beat==input_beats-1)begin
      if(s_axis_tlast)state<=CLEAR;else begin error<=1;slot_valid[cache_select]<=0;payload<=0;status<=2;state<=DRAIN;end
     end else if(s_axis_tlast)begin error<=1;slot_valid[cache_select]<=0;payload<=0;status<=2;state<=RESPONSE;end
     else write_beat<=write_beat+1;
    end
    CLEAR: begin issued<=0;read_address<=time_tile;upper_group<=0;state<=COMPUTE;end
    COMPUTE:begin
     if(issue)begin issued<=issued+1;read_address<=read_address+tiles;end
     if(product_valid && product_last)begin output_beat<=0;quant_feed<=0;state<=!header_sent?RESPONSE:is_quant?QUANT:RAW;end
    end
    RESPONSE: if(m_axis_tvalid && m_axis_tready)begin
     if(payload)begin header_sent<=1;output_beat<=0;quant_feed<=0;state<=is_quant?QUANT:RAW;end
     else begin state<=HEADER;done<=1;end
    end
    QUANT,RAW:begin
     if(state==QUANT && quant_feed<16 && qready && !error)quant_feed<=quant_feed+1;
     if(state==QUANT && qvalid && qerror && m_axis_tready)begin error<=1;slot_valid[cache_select]<=0;end
     if(m_axis_tvalid && m_axis_tready)begin
      if(output_beat==(is_quant?15:63))begin
       output_beat<=0;quant_feed<=0;
       if(!upper_group && pair_has_upper)begin upper_group<=1;state<=is_quant?QCLEAR:RAW;end
       else if(!last_group)begin compute_group<=compute_group+2;upper_group<=0;state<=error?QUANT:CLEAR;end
       else if(!last_time)begin time_tile<=time_tile+1;compute_group<=0;upper_group<=0;state<=error?QUANT:CLEAR;end
       else if(is_bulk)begin state<=TRAILER;counter_snapshot<={active_cycles+1,output_stalls,input_stalls,mac_cycles};end
       else begin state<=HEADER;done<=1;end
      end else output_beat<=output_beat+1;
     end
    end
    DRAIN: if(accept && s_axis_tlast)state<=RESPONSE;
    QCLEAR:begin quant_feed<=0;output_beat<=0;state<=QUANT;end
    TRAILER: if(m_axis_tvalid && m_axis_tready)begin state<=HEADER;done<=1;end
    default:begin state<=HEADER;slot_valid[cache_select]<=0;error<=1;end
   endcase
  end
 end
endmodule
