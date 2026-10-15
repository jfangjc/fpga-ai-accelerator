// Protocol 18 emits residual stage zero, preserves original input, then emits the final main stage.
// Op17 streams every stage's PW and DW weights inside one block transaction.
// Two-entry read pipeline: one retained activation beat per clock.
// Multi-pair sequencer over the verified 32-slot shortwide engine.
// Op 15: header, {count[23:16],valid_t[12:0]}, N descriptors,
// (N-1)*256 LUT bytes, padded channel-major input. One final response.
// Descriptor: slot[4:0], masked byte[15:8], tag[63:32]; other bits zero.
// A single activation bank is safe: the inner engine consumes the entire
// input before producing pointwise output. Partial output tiles transpose
// through 16 registers before overwriting this bank.
module qn_cached_axis #(parameter MAX_K=256,parameter MAX_M=256)(
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
 output wire busy,output reg done,output wire error
);
 localparam IDLE=0,PASS=1,META=2,DESC=3,LUTIN=4,INPUTS=5,LUTREAD=6,
   LUTWAIT=7,LUTCOPY=8,RUNHEAD=9,RUNMETA=10,READ=11,READWAIT=12,
   SEND=13,ACK=14,OUTPUTS=15,WRITE=16,COUNTER=17,FOOTER=18,DRAIN=19,BADACK=20,LOADHEAD=21,LOADDATA=22,LOADACK=23,LOADWAIT=24;
 reg [4:0] state;
 reg [8:0] times,valid_times;
 reg [4:0] tiles;
 reg [5:0] count,step,desc_index;
 reg [4:0] load_stage,load_next;reg load_phase;reg [12:0] load_index;
 reg [8:0] lut_beats,lut_index;
 reg [3:0] lut_lane,row,group_index,time_row;
 reg [4:0] tile_index;
 reg [12:0] input_index,input_beats,read_index,fetch_index;
 reg [127:0] replay_head,replay_tail;
 reg [1:0] replay_count;
 reg replay_pending;
 wire replay_pop=state==SEND && replay_count!=0 && inner_ready;
 wire [2:0] replay_occupied={1'b0,replay_count}+{2'b0,replay_pending};
 wire replay_fetch=state==SEND && fetch_index<input_beats &&
   (replay_occupied<2 || (replay_pop && replay_occupied==2));
 reg [31:0] outer_tag,active_cycles,snapshot_cycles,issue_cycles,input_stalls,output_stalls,status;
 reg bad,branch_mode;
 wire branch_step=branch_mode && step==0;
 wire emit_step=final_step || branch_step;
 reg [63:0] descriptors[0:31];
 reg [7:0] lookup[0:255];
 reg [127:0] transpose[0:15];
 wire [31:0] load_tag=descriptors[load_stage][63:32];
 wire [15:0] load_dimension=load_phase?{10'b0,descriptors[load_stage][21:16]}:16'd256;
 wire [12:0] load_last=load_phase?13'd1023:13'd4351;
 wire final_step=step==count-1;
 wire accept=s_axis_tvalid && s_axis_tready;
 wire inner_ready,inner_busy,inner_error;
 wire raw_valid,raw_last,raw_done;wire [127:0] raw_data;
 reg inner_valid,inner_last;reg [127:0] inner_data;
 reg completion_pending;
 wire inner_done=completion_pending || raw_done;
 wire raw_ready=!inner_valid || inner_output_ready;
 // One-beat elastic output buffer cuts the pointwise mux to sequencer path.
 // Completion can precede consumption of the buffered load ACK; retain it.
 always @(posedge aclk)begin
   if(!aresetn)begin
     inner_valid<=0;inner_last<=0;inner_data<=0;completion_pending<=0;
   end else begin
     if(raw_ready)begin
       inner_valid<=raw_valid;
       if(raw_valid)begin inner_data<=raw_data;inner_last<=raw_last;end
     end
     if(raw_done)completion_pending<=1;
     if((state==LOADWAIT && inner_done) || (state==PASS && inner_done) ||
        ((state==LOADHEAD || state==RUNHEAD) && inner_ready))completion_pending<=0;
   end
 end
 wire [127:0] activation_data,lut_data;
 reg [127:0] write_data;
 wire activation_write=(state==INPUTS && accept) || state==WRITE;
 wire [11:0] activation_addr=state==INPUTS?input_index[11:0]:
                   ((group_index*16+row)*tiles+tile_index);
 wire [127:0] activation_input=state==INPUTS?s_axis_tdata:write_data;
 wire [4:0] selected_slot=descriptors[step[4:0]][4:0];
 wire [31:0] selected_tag=descriptors[step[4:0]][63:32];
 wire [7:0] mask_value=descriptors[step[4:0]][15:8];
 wire inner_input_valid=state==LOADHEAD || state==RUNHEAD || state==RUNMETA ||
   (state==LOADDATA && s_axis_tvalid) || (state==SEND && replay_count!=0);
 wire [127:0] inner_input_data=state==LOADHEAD?
   {load_tag,16'd0,16'd256,load_dimension,3'b0,load_stage,(load_phase?8'd13:8'd12),32'h32434e51}:
   state==RUNHEAD?
   {selected_tag,7'b0,times,16'd256,16'd256,3'b0,selected_slot,8'd14,32'h32434e51}:
   state==RUNMETA?{119'b0,valid_times}:state==SEND?replay_head:s_axis_tdata;
 wire inner_input_last=state==LOADDATA?load_index==load_last:
   state==LOADHEAD || state==RUNHEAD || state==RUNMETA?1'b0:
   state==SEND?read_index==input_beats-1:1'b0;
 wire inner_output_ready=state==LOADACK?1'b1:
   state==ACK?(emit_step?m_axis_tready:1'b1):state==OUTPUTS?(emit_step?m_axis_tready:1'b1):state==COUNTER;
 assign s_axis_tready=aresetn && (state==IDLE?1'b1:state==LOADDATA?inner_ready:
   state==META || state==DESC || state==LUTIN || state==INPUTS || state==DRAIN);
 assign m_axis_tvalid=aresetn && (state==PASS?inner_valid:state==BADACK || state==FOOTER ||
   ((state==ACK || state==OUTPUTS) && emit_step && inner_valid));
 assign m_axis_tdata=state==BADACK?{outer_tag,16'd256,16'd256,status,32'h32524e51}:
   state==FOOTER?{snapshot_cycles,output_stalls,input_stalls,issue_cycles}:
   state==ACK && emit_step?{outer_tag,inner_data[95:0]}:inner_data;
 assign m_axis_tlast=state==PASS?inner_last:state==BADACK || state==FOOTER;
 assign busy=state!=IDLE;
 assign error=bad || inner_error;
 always @* begin
   write_data=0;
   for(integer lane=0;lane<16;lane=lane+1)
     write_data[lane*8+:8]=(tile_index*16+lane>=valid_times)?mask_value:
       lookup[transpose[lane][row*8+:8]];
 end
 xpm_memory_sdpram #(.MEMORY_SIZE(4096*128),.MEMORY_PRIMITIVE("block"),
   .CLOCKING_MODE("common_clock"),.ECC_MODE("no_ecc"),.USE_MEM_INIT(0),
   .WRITE_DATA_WIDTH_A(128),.BYTE_WRITE_WIDTH_A(128),.ADDR_WIDTH_A(12),
   .READ_DATA_WIDTH_B(128),.ADDR_WIDTH_B(12),.READ_LATENCY_B(1),.WRITE_MODE_B("read_first"),.SIM_ASSERT_CHK(1)) activation_bank(
   .clka(aclk),.clkb(aclk),.sleep(1'b0),.ena(activation_write),.wea(activation_write),
   .addra(activation_addr),.dina(activation_input),.injectsbiterra(1'b0),.injectdbiterra(1'b0),
   .rstb(1'b0),.enb(replay_fetch),.regceb(1'b1),.addrb(fetch_index[11:0]),
   .doutb(activation_data),.sbiterrb(),.dbiterrb());
 xpm_memory_sdpram #(.MEMORY_SIZE(512*128),.MEMORY_PRIMITIVE("block"),
   .CLOCKING_MODE("common_clock"),.ECC_MODE("no_ecc"),.USE_MEM_INIT(0),
   .WRITE_DATA_WIDTH_A(128),.BYTE_WRITE_WIDTH_A(128),.ADDR_WIDTH_A(9),
   .READ_DATA_WIDTH_B(128),.ADDR_WIDTH_B(9),.READ_LATENCY_B(1),.WRITE_MODE_B("read_first"),.SIM_ASSERT_CHK(1)) lut_bank(
   .clka(aclk),.clkb(aclk),.sleep(1'b0),.ena(state==LUTIN && accept),.wea(state==LUTIN && accept),
   .addra(lut_index),.dina(s_axis_tdata),.injectsbiterra(1'b0),.injectdbiterra(1'b0),
   .rstb(1'b0),.enb(state==LUTREAD),.regceb(1'b1),.addrb({step[4:0],lut_lane}),
   .doutb(lut_data),.sbiterrb(),.dbiterrb());
 qn_branch512_ab_inner #(.MAX_K(MAX_K),.MAX_M(MAX_M)) engine(
   .aclk(aclk),.aresetn(aresetn),.allow_wrap(branch_step),.s_axis_tdata(inner_input_data),.s_axis_tvalid(inner_input_valid),
   .s_axis_tready(inner_ready),.s_axis_tlast(inner_input_last),.m_axis_tdata(raw_data),
   .m_axis_tvalid(raw_valid),.m_axis_tready(raw_ready),.m_axis_tlast(raw_last),
   .busy(inner_busy),.done(raw_done),.error(inner_error));
 always @(posedge aclk) begin
   if(!aresetn) begin
     state<=IDLE;done<=0;bad<=0;branch_mode<=0;load_stage<=0;load_next<=LOADHEAD;load_phase<=0;load_index<=0;status<=0;times<=0;valid_times<=0;tiles<=0;count<=0;step<=0;
     desc_index<=0;lut_beats<=0;lut_index<=0;lut_lane<=0;row<=0;group_index<=0;time_row<=0;
     tile_index<=0;input_index<=0;input_beats<=0;read_index<=0;outer_tag<=0;
     fetch_index<=0;replay_count<=0;replay_pending<=0;replay_head<=0;replay_tail<=0;
     active_cycles<=0;snapshot_cycles<=0;issue_cycles<=0;input_stalls<=0;output_stalls<=0;
   end else begin
     done<=0;
     if(state!=IDLE && state!=PASS && state!=FOOTER)active_cycles<=active_cycles+1;
     if((state==INPUTS || state==LOADDATA) && !s_axis_tvalid)input_stalls<=input_stalls+1;
     if(m_axis_tvalid && !m_axis_tready && state!=PASS && state!=FOOTER)output_stalls<=output_stalls+1;
     if(state==SEND)begin
       replay_pending<=replay_fetch;
       if(replay_fetch)fetch_index<=fetch_index+1;
       case({replay_pending,replay_pop})
         2'b10:begin
           if(replay_count==0)replay_head<=activation_data;else replay_tail<=activation_data;
           replay_count<=replay_count+1;
         end
         2'b01:begin replay_head<=replay_tail;replay_count<=replay_count-1;end
         2'b11:begin
           if(replay_count==1)replay_head<=activation_data;
           else begin replay_head<=replay_tail;replay_tail<=activation_data;end
         end
         default:begin end
       endcase
     end
`ifndef SYNTHESIS
     if(state==SEND && (replay_occupied>2 || (replay_pop && read_index==input_beats-1 && replay_pending)))
       $fatal(1,"Retained replay FIFO invariant failed");
`endif
     case(state)
       IDLE:if(accept)begin
         bad<=0;status<=0;
         begin
           branch_mode<=s_axis_tdata[39:32]==18;outer_tag<=s_axis_tdata[127:96];times<=s_axis_tdata[88:80];tiles<=(s_axis_tdata[95:80]+15)>>4;
           input_beats<=256*((s_axis_tdata[95:80]+15)>>4);
           active_cycles<=0;issue_cycles<=0;input_stalls<=0;output_stalls<=0;step<=0;
           if((s_axis_tdata[39:32]!=17 && s_axis_tdata[39:32]!=18) || s_axis_tdata[31:0]!=32'h32434e51 || s_axis_tdata[79:40]!={16'd256,16'd256,8'b0} ||
             s_axis_tdata[95:80]==0 || s_axis_tdata[95:80]>256 || s_axis_tdata[127:96]==0 || s_axis_tlast)
               begin bad<=1;status<=1;state<=s_axis_tlast?BADACK:DRAIN;end
           else state<=META;
         end
       end
       PASS:if(inner_done)begin state<=IDLE;done<=1;end
       META:if(accept)begin
         if(s_axis_tlast || s_axis_tdata[127:24]!=0 || s_axis_tdata[15:13]!=0 ||
           s_axis_tdata[12:0]>times || s_axis_tdata[23:16]==0 || s_axis_tdata[23:16]>8 || (branch_mode && s_axis_tdata[23:16]<2))
             begin bad<=1;status<=1;state<=s_axis_tlast?BADACK:DRAIN;end
         else begin valid_times<=s_axis_tdata[8:0];count<=s_axis_tdata[21:16];desc_index<=0;
           lut_beats<=(s_axis_tdata[23:16]-1)*16;state<=DESC;end
       end
       DESC:if(accept)begin
         descriptors[desc_index[4:0]]<=s_axis_tdata[63:0];
         if(s_axis_tlast || s_axis_tdata[127:64]!=0 || s_axis_tdata[31:22]!=0 || s_axis_tdata[21:16]==0 || s_axis_tdata[21:16]>41 || !s_axis_tdata[16] ||
           s_axis_tdata[7:5]!=0 || s_axis_tdata[4:0]!=desc_index || s_axis_tdata[63:32]==0)
             begin bad<=1;status<=1;state<=s_axis_tlast?BADACK:DRAIN;end
         else if(desc_index==count-1)begin lut_index<=0;input_index<=0;load_stage<=0;load_phase<=0;load_index<=0;state<=LOADHEAD;end
         else desc_index<=desc_index+1;
       end
       LOADHEAD:if(inner_ready)begin load_index<=0;state<=LOADDATA;end
       LOADDATA:if(accept)begin
         if(s_axis_tlast)begin bad<=1;status<=2;state<=BADACK;end
         else if(load_index==load_last)state<=LOADACK;
         else load_index<=load_index+1;
       end
       LOADACK:if(inner_valid && inner_output_ready)begin
         if(inner_data[31:0]!=32'h32524e51 || inner_data[63:32]!=0 ||
           inner_data[95:64]!={load_dimension,16'd256} || inner_data[127:96]!=load_tag || !inner_last)
             begin bad<=1;status<=3;state<=DRAIN;end
         else begin
           state<=LOADWAIT;
           if(!load_phase)begin load_phase<=1;load_next<=LOADHEAD;end
           else if(load_stage==count-1)begin step<=0;load_next<=count==1?INPUTS:LUTIN;end
           else begin load_stage<=load_stage+1;load_phase<=0;load_next<=LOADHEAD;end
         end
       end
       LOADWAIT:if(inner_done)state<=load_next;
       LUTIN:if(accept)begin
         if(s_axis_tlast)begin bad<=1;status<=2;state<=BADACK;end
         else if(lut_index==lut_beats-1)begin input_index<=0;state<=INPUTS;end
         else lut_index<=lut_index+1;
       end
       INPUTS:if(accept)begin
         if(s_axis_tlast!=(input_index==input_beats-1))begin bad<=1;status<=2;state<=s_axis_tlast?BADACK:DRAIN;end
         else if(input_index==input_beats-1)begin lut_lane<=0;state<=(count==1 || branch_mode)?RUNHEAD:LUTREAD;end
         else input_index<=input_index+1;
       end
       LUTREAD:state<=LUTWAIT;
       LUTWAIT:state<=LUTCOPY;
       LUTCOPY:begin
         for(integer i=0;i<16;i=i+1)lookup[lut_lane*16+i]<=lut_data[i*8+:8];
         if(lut_lane==15)state<=RUNHEAD;else begin lut_lane<=lut_lane+1;state<=LUTREAD;end
       end
       RUNHEAD:if(inner_ready)state<=RUNMETA;
       RUNMETA:if(inner_ready)begin
         read_index<=0;fetch_index<=0;replay_count<=0;replay_pending<=0;state<=SEND;
       end
       SEND:if(replay_pop)begin
         if(read_index==input_beats-1)state<=ACK;
         else read_index<=read_index+1;
       end
       ACK:if(inner_valid && inner_output_ready)begin
         if(inner_data[31:0]!=32'h32524e51 || inner_data[63:32]!=0 ||
           inner_data[95:64]!={16'd256,16'd256} || inner_data[127:96]!=selected_tag)bad<=1;
         group_index<=0;tile_index<=0;time_row<=0;state<=OUTPUTS;
       end
       OUTPUTS:if(inner_valid && inner_output_ready)begin
         if(emit_step)begin
           if(time_row==15)begin time_row<=0;
             if(group_index==15)begin group_index<=0;if(tile_index==tiles-1)state<=COUNTER;else tile_index<=tile_index+1;end
             else group_index<=group_index+1;
           end else time_row<=time_row+1;
         end else begin
           transpose[time_row]<=inner_data;
           if(time_row==15)begin time_row<=0;row<=0;state<=WRITE;end
           else time_row<=time_row+1;
         end
       end
       WRITE:begin
         if(row==15)begin row<=0;
           if(group_index==15)begin group_index<=0;if(tile_index==tiles-1)state<=COUNTER;else begin tile_index<=tile_index+1;state<=OUTPUTS;end end
           else begin group_index<=group_index+1;state<=OUTPUTS;end
         end else row<=row+1;
       end
       COUNTER:if(inner_valid && inner_output_ready)begin
         issue_cycles<=issue_cycles+inner_data[31:0];
         input_stalls<=input_stalls+inner_data[63:32];output_stalls<=output_stalls+inner_data[95:64];
         if(!inner_last)bad<=1;
         if(final_step)begin snapshot_cycles<=active_cycles+1;state<=FOOTER;end
         else begin step<=step+1;lut_lane<=0;state<=step==count-2?RUNHEAD:LUTREAD;end
       end
       FOOTER:if(m_axis_tready)begin state<=IDLE;done<=1;end
       DRAIN:if(accept && s_axis_tlast)state<=BADACK;
       BADACK:if(m_axis_tready)begin state<=IDLE;done<=1;end
       default:begin state<=IDLE;bad<=1;end
     endcase
   end
 end
endmodule
