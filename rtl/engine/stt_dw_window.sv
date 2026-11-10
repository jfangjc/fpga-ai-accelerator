// One channel's depthwise line buffer. The group preload writes 128-bit
// words; a single synchronous BRAM read port primes/refills a rolling
// three-word window. Serial startup alignment and fixed one/two-byte tap
// shifts avoid both a full-row gather mux and a variable byte barrel shifter.
module stt_dw_window import stt_engine_pkg::*; (
 input logic clk, we,
 input logic [5:0] waddr,
 input logic [127:0] wdata,
 input logic prime, align_byte, issue,
 input logic [2:0] prime_count,
 input logic signed [11:0] base,
 input logic [1:0] dilation,
 output logic [255:0] samples
);
 (* ram_style = "block" *) logic [127:0] cache[64];
 logic [127:0] cache_q;
 logic [383:0] window_bytes, slid;
 logic signed [11:0] read_word;
 logic read_en, read_ok;
 logic cross_word;
 assign read_en=issue || (prime && prime_count<4);
 // Both addends must remain signed: an unsigned conditional arm would
 // turn the padding address's arithmetic right shift into a logical one.
 assign read_word=(base >>> 4)+(prime ? $signed({9'd0,prime_count}) : 12'sd3);
 assign cross_word=({1'b0,base[3:0]}+{3'b0,dilation})>=5'd16;
 assign slid=dilation==2 ? window_bytes >> 16 : window_bytes >> 8;
 always_ff @(posedge clk) begin
  if(we) cache[waddr]<=wdata;
  // Keep masking out of the RAM output register to preserve BRAM inference.
  if(read_en) begin
   cache_q<=cache[read_word[5:0]];
   read_ok<=read_word>=0 && read_word<12'(MAX_NT);
  end
  if(prime) begin
   case(prime_count)
    1: window_bytes[127:0]<=read_ok ? cache_q : 128'd0;
    2: window_bytes[255:128]<=read_ok ? cache_q : 128'd0;
    3: window_bytes[383:256]<=read_ok ? cache_q : 128'd0;
    default: ;
   endcase
  end
  if(align_byte) window_bytes<=window_bytes >> 8;
  if(issue) begin
   samples<=window_bytes[255:0];
   window_bytes<=slid;
   // The next word has been prefetched since the preceding boundary.
   // PRIME also prefetches it, covering a crossing on the very first tap.
   if(cross_word) begin
    // Dilation 2 at an odd address crosses the boundary by one byte.
    // Insert the next aligned word at byte 31 in that case, else at 32.
    if(dilation==2 && base[0])
     window_bytes<={8'd0,(read_ok ? cache_q : 128'd0),slid[247:0]};
    else window_bytes<={(read_ok ? cache_q : 128'd0),slid[255:0]};
   end
  end
 end
endmodule
