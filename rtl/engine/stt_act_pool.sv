// Shared activation storage: ten independently addressed 160 KiB slices.
// Layer map: two regions of five slices. Network map: five of two slices.
module stt_act_pool
    import stt_engine_pkg::*;
(
    input logic clk, network_mode,
    input logic [2:0] source_bank, second_bank, dest_bank,
    input logic a_we, b_we, a_re, b_re,
    input logic [ACT_AW-1:0] a_wa, b_wa, a_ra, b_ra,
    input logic [127:0] a_wd,b_wd,
    output logic [127:0] a_rd,b_rd
);
    localparam int WORDS=ACT_DEPTH/5;  // 10,240 = 5 x 2,048 words per slice
    // Decompose a pool word address without a general divider:
    // addr = hi*2048 + lo with hi = addr[15:11] (0..31), so
    // addr = (hi/5)*WORDS + ((hi%5)*2048 + lo). hi/5 and hi%5 are 5-bit
    // constant operations (a few LUTs), keeping URAM address paths shallow.
    function automatic logic [16:0] split(input logic [ACT_AW-1:0] a);
        logic [4:0] hi;
        hi = a[15:11];
        split = {3'(hi / 5), 3'(hi % 5), a[10:0]};  // {slice_in_bank, offset[13:0]}
    endfunction
    logic [16:0] aw_s, bw_s, ar_s, br_s;
    logic [3:0] aw_slice,bw_slice,ar_slice,br_slice;
    logic [3:0] ar_delay[ACT_LAT],br_delay[ACT_LAT];
    logic [9:0][127:0] rd;
    assign aw_s=split(a_wa);
    assign bw_s=split(b_wa);
    assign ar_s=split(a_ra);
    assign br_s=split(b_ra);
    assign aw_slice=4'(int'(source_bank)*(network_mode ? 2 : 5)+int'(aw_s[16:14]));
    assign bw_slice=4'(int'(dest_bank)*(network_mode ? 2 : 5)+int'(bw_s[16:14]));
    assign ar_slice=4'(int'(source_bank)*(network_mode ? 2 : 5)+int'(ar_s[16:14]));
    assign br_slice=4'(int'(second_bank)*(network_mode ? 2 : 5)+int'(br_s[16:14]));
    always_ff @(posedge clk) begin
        ar_delay[0]<=ar_slice; br_delay[0]<=br_slice;
        for(int i=1;i<ACT_LAT;i++) begin ar_delay[i]<=ar_delay[i-1]; br_delay[i]<=br_delay[i-1]; end
    end
    assign a_rd=rd[ar_delay[ACT_LAT-1]];
    assign b_rd=rd[br_delay[ACT_LAT-1]];
    for(genvar b=0;b<10;b++) begin : g_slice
        logic we,re;
        logic [13:0] wa,ra;
        logic [127:0] wd;
        assign we=(a_we && aw_slice==4'(b)) || (b_we && bw_slice==4'(b));
        assign wa=b_we && bw_slice==4'(b) ? bw_s[13:0] : aw_s[13:0];
        assign wd=b_we && bw_slice==4'(b) ? b_wd : a_wd;
        assign re=(a_re && ar_slice==4'(b)) || (b_re && br_slice==4'(b));
        assign ra=a_re && ar_slice==4'(b) ? ar_s[13:0] : br_s[13:0];
        stt_sdp_ram #(.WIDTH(128),.DEPTH(WORDS),.LATENCY(ACT_LAT),.STYLE("ultra")) ram (
            .clk,.we,.waddr(wa),.wdata(wd),.re,.raddr(ra),.rdata(rd[b]));
    end
endmodule

