// Sixteen channel by sixteen frame depthwise pipeline.
module stt_dw_ctrl
    import stt_engine_pkg::*;
#(parameter int FRAME_LANES=16)(
 input logic clk, rst, start,
 /* verilator lint_off UNUSEDSIGNAL */
 input layer_cfg_t cfg,
 /* verilator lint_on UNUSEDSIGNAL */
 input logic [1:0] w_full,
 output logic [1:0] w_release,
 output logic w_re, w_half,
 output logic [9:0] w_k,
 input logic [255:0] w_rdata,
 output logic act_re,
 output logic [ACT_AW-1:0] act_raddr,
 input logic [127:0] act_rdata,
 input logic [31:0][31:0] bias,
 output tile_tag_t row_tag,
 output logic [4:0] row_idx,
 output logic row_valid, finished, busy, overflow,
 output logic [15:0][SUM_W-1:0] row_sum,
 output logic weight_wait,
 output logic [31:0] issue_cycles
);
 // Sixteen channel caches amortize the single URAM port over all output tiles.
 // Weights and activations load once per channel group, with pipelined reads.
 initial if(FRAME_LANES!=16) $fatal(1, "depthwise requires 16 frame lanes");
 localparam int C=16;
 typedef enum logic [3:0] {IDLE, WAIT_W, LOAD, WEIGHTS, INIT, PRIME, ALIGN, ISSUE, DRAIN, EMIT, NEXT, DONE} state_t;
 state_t state;
 integer ch, ft, count, emit_ch, tap;
 logic [127:0] weights[87];
 logic [ACT_LAT-1:0] av, wv;
 integer wa[ACT_LAT];
 logic [3:0] load_channel, channel_delay[ACT_LAT];
 logic [5:0] load_tile, tile_delay[ACT_LAT];
 logic [ACT_AW-1:0] load_addr;
 logic signed [8:0] operand[C][16];
 logic [255:0] gather[C];
 logic [2:0] prime_count;
 logic [3:0] align_left;
 // Tap positions advance incrementally: no multiplies on the cache address.
 logic signed [11:0] gather_base, base_q, ft_base, neg_pad, ft_step, pos_lim;
 logic [1:0] dilation_q;
 logic [5:0] ntout_m1;
 logic [ACT_AW-1:0] otile_base;
 logic [C-1:0] chan_ok;
 logic [C-1:0][15:0] ovf_lane;
 logic signed [7:0] gather_weight[C];
 logic signed [7:0] weight_q[C];
 (* use_dsp = "yes" *) logic signed [32:0] product[C][16];
 (* use_dsp = "yes" *) logic signed [32:0] acc[C][16];
 logic v0,v1,v2,v3,last0,last1,last2;
 logic first0,first1,first2;
 assign weight_wait=state==WAIT_W && !w_full[w_half];
 assign act_re=state==LOAD && count<C*int'(cfg.nt);
 // Channels are loaded consecutively. Carry explicit row/tile tags rather
 // than inferring a variable divider and remainder network on the RAM port.
 assign act_raddr=load_addr;
 assign w_re=state==WEIGHTS && count<int'(cfg.taps);
 assign w_half=ch[5];
 assign w_k=10'(count);
 assign row_idx=5'(ch+emit_ch);
 assign row_tag='{otile:5'(ch/32),base:otile_base+ACT_AW'(ft)};
 assign row_valid=state==EMIT && ch+emit_ch<int'(cfg.co);
 assign finished=state==DONE;
 assign busy=state!=IDLE && state!=DONE;
 for(genvar f=0;f<16;f++) begin : g_sum
  assign row_sum[f]=acc[emit_ch][f];
 end
 // Registered gather, multiply, then accumulate: one complete 256-MAC tap
 // per clock. Prefix overflow remains sticky even when later taps cancel.
 for(genvar c=0;c<C;c++) begin : g_channel
  stt_dw_window window_cache (
   .clk, .we(av[ACT_LAT-1] && channel_delay[ACT_LAT-1]==4'(c)),
   .waddr(tile_delay[ACT_LAT-1]), .wdata(act_rdata),
   .prime(state==PRIME), .align_byte(state==ALIGN), .issue(state==ISSUE), .prime_count,
   .base(gather_base), .dilation(dilation_q), .samples(gather[c]));
  for(genvar f=0;f<16;f++) begin : g_frame
   logic signed [11:0] pos;
   logic [7:0] sample_byte;
   logic signed [32:0] feedback;
   logic signed [31:0] bias1,bias2;
   assign pos=base_q+12'(f)*12'(cfg.stride);
   // The byte window has already aligned the first sample. Each lane
   // only chooses its fixed stride-1 or stride-2 byte.
   assign sample_byte=cfg.stride==2 ? gather[c][16*f+:8] : gather[c][8*f+:8];
   // Match stt_mac_array's DSP A/B, M, P template. A separate INIT load
   // on P prevents MAC inference; the first product instead selects bias
   // on the DSP C feedback path. Keep M wide so the adder is absorbed too.
   assign feedback=first2 ? 33'(bias2) : acc[c][f];
   always_ff @(posedge clk) begin
    if(v0) begin
     bias1<=$signed(bias[{ch[4],4'(c)}]);
     operand[c][f]<=0;
     if(pos>=0 && pos<pos_lim)
      operand[c][f]<=$signed({cfg.in_signed && sample_byte[7],sample_byte});
    end
    if(v1) begin
     bias2<=bias1;
     product[c][f]<=operand[c][f]*weight_q[c];
    end
    if(v2) acc[c][f]<=feedback+product[c][f];
    // Overflow is checked on the registered accumulator: a 33-bit prefix
    // leaves INT32 range exactly when its top two bits differ.
    ovf_lane[c][f]<=!rst && v3 && chan_ok[c] && acc[c][f][32]!=acc[c][f][31];
   end
  end
  always_ff @(posedge clk) begin
   if(state==ISSUE) gather_weight[c]<=$signed(weights[tap][c*8+:8]);
   if(v0) weight_q[c]<=gather_weight[c];
  end
 end
 always_ff @(posedge clk) begin
  av[0]<=act_re; wv[0]<=w_re; wa[0]<=count;
  channel_delay[0]<=load_channel; tile_delay[0]<=load_tile;
  for(int i=1;i<ACT_LAT;i++) begin
   av[i]<=av[i-1];wv[i]<=wv[i-1];wa[i]<=wa[i-1];
   channel_delay[i]<=channel_delay[i-1]; tile_delay[i]<=tile_delay[i-1];
  end
  if(wv[ACT_LAT-1]) weights[wa[ACT_LAT-1]]<=ch[4] ? w_rdata[255:128] : w_rdata[127:0];
  // Layer constants are stable from start; register their derived forms.
  neg_pad<=-12'(cfg.padding); ft_step<=12'(cfg.stride)<<4; dilation_q<=cfg.dilation;
  pos_lim<=12'(cfg.valid<cfg.t ? cfg.valid : cfg.t);
  ntout_m1<=6'((int'(cfg.tout)+15)/16-1);
  for(int c=0;c<C;c++) chan_ok[c]<=ch+c<int'(cfg.co);
  if(state==INIT) gather_base<=ft_base;
  else if(state==ISSUE) gather_base<=gather_base+12'(dilation_q);
  base_q<=gather_base;
  v0<=state==ISSUE;v1<=v0;v2<=v1;v3<=v2;
  last0<=state==ISSUE && tap==int'(cfg.taps)-1;last1<=last0;last2<=last1;
  first0<=state==ISSUE && tap==0;first1<=first0;first2<=first1;
  w_release<=0;
  if(rst) begin
   state<=IDLE;overflow<=0;issue_cycles<=0;av<=0;wv<=0;v0<=0;v1<=0;v2<=0;v3<=0;last0<=0;last1<=0;last2<=0;
  end else begin
   if(act_re) begin
    load_addr<=load_addr+1'b1;
    if(load_tile==cfg.nt-1'b1) begin load_tile<=0;load_channel<=load_channel+1'b1;end
    else load_tile<=load_tile+1'b1;
   end
   if(|ovf_lane) overflow<=1;
   case(state)
    IDLE: if(start) begin ch<=0;ft<=0;ft_base<=-12'(cfg.padding);otile_base<=0;load_addr<=0;load_channel<=0;load_tile<=0;state<=WAIT_W;end
    WAIT_W: if(w_full[w_half]) begin count<=0;state<=LOAD;end
    LOAD: begin
     count<=count+1;
     if(count==C*int'(cfg.nt)+ACT_LAT) begin count<=0;state<=WEIGHTS;end
    end
    WEIGHTS: begin
     count<=count+1;
     if(count==int'(cfg.taps)+ACT_LAT) state<=INIT;
    end
    INIT: begin tap<=0;prime_count<=0;align_left<=ft_base[3:0];state<=PRIME;end
    PRIME: if(prime_count==4) state<=align_left==0 ? ISSUE : ALIGN;
           else prime_count<=prime_count+1'b1;
    ALIGN: begin
     align_left<=align_left-1'b1;
     if(align_left==1) state<=ISSUE;
    end
    ISSUE: begin
     issue_cycles<=issue_cycles+1;
     if(tap==int'(cfg.taps)-1) state<=DRAIN;
     else tap<=tap+1;
    end
    DRAIN: if(last2) begin emit_ch<=0;state<=EMIT;end
    EMIT: if(emit_ch==C-1) state<=NEXT;else emit_ch<=emit_ch+1;
    NEXT: begin
     if(6'(ft)<ntout_m1) begin ft<=ft+1;ft_base<=ft_base+ft_step;state<=INIT;end
     else begin
      ft<=0;ft_base<=neg_pad;
      if(ch%32==16) otile_base<=otile_base+(ACT_AW'(ntout_m1)+1'b1)*32;
      if(ch%32==16 || ch+C>=int'(cfg.co)) w_release<=w_half ? 2'b10 : 2'b01;
      if(ch+C>=int'(cfg.co)) state<=DONE;
      else begin
       ch<=ch+C;count<=0;
       if(ch%32==16) state<=WAIT_W;else state<=LOAD;
      end
     end
    end
    default: ;
   endcase
  end
 end
endmodule



