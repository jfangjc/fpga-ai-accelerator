// Response packer: emits the four-beat QNS2 header and, for a successful
// RUN_LAYER, the output tensor as contiguous channel-major [Co][T] bytes
// zero-padded to 16 bytes, with TLAST on the final beat. Output words
// (channel, 16-frame tile) are read sequentially from the output bank into a
// small FIFO (credit-limited, so the bank latency never overflows it) and
// realigned: each word contributes 16 bytes, or last_n for a row's final
// tile, appended at byte offset cnt of a partial beat. TKEEP is always all
// ones. The output register follows AXI4-Stream (data stable while stalled).
module stt_resp_packer
    import stt_engine_pkg::*;
#(
    parameter int RD_LAT = ACT_LAT,
    parameter int FIFO_D = 8
) (
    input  logic               clk,
    input  logic               rst,
    input  logic               start,
    input  logic               with_output,
    // Shared configuration struct; this module reads only some fields.
    /* verilator lint_off UNUSEDSIGNAL */
    input  layer_cfg_t         cfg,
    /* verilator lint_on UNUSEDSIGNAL */
    input  logic [15:0][31:0]  header,
    // Output bank read port
    output logic               re,
    output logic [ACT_AW-1:0]  raddr,
    input  logic [127:0]       rdata,
    // Response stream (AXI4-Stream master)
    output logic [127:0]       m_tdata,
    output logic               m_tvalid,
    input  logic               m_tready,
    output logic               m_tlast,
    output logic               done         // final beat accepted
);
    localparam int FW = $clog2(FIFO_D);
    typedef enum logic [2:0] {K_IDLE, K_HDR, K_DATA, K_FLUSH, K_WAIT} kstate_t;
    kstate_t state;
    logic [1:0]  hb;                 // header beat
    logic        slot;               // output register may load
    // Read side
    logic [10:0] rc;                 // channel of next read
    logic [5:0]  rf;                 // tile of next read
    logic [FW:0] reserved;           // words read but not yet popped
    logic [RD_LAT-1:0] rv;
    // FIFO
    logic [127:0] fifo [FIFO_D];
    logic [FW-1:0] wp, rp;
    logic [FW:0]   fcount;
    logic          push, pop;
    // Pop side / byte merge
    logic [10:0] pc;
    logic [5:0]  pf;
    logic [3:0]  cnt;                // bytes pending in lo
    logic [7:0]  lo [16];
    logic [7:0]  word [16];
    logic [7:0]  rot [16];
    logic [7:0]  beat [16];
    logic [7:0]  nlo [16];
    logic [4:0]  n, total;
    logic        emit, last_word;

    assign slot = !m_tvalid || m_tready;
    assign re = state == K_DATA && rc != cfg.co && reserved != (FW+1)'(FIFO_D);
    assign push = rv[RD_LAT-1];
    assign pop = state == K_DATA && fcount != 0 && slot;

    always_comb begin
        n = (pf == cfg.nt - 6'd1) ? cfg.last_n : 5'd16;
        total = {1'b0, cnt} + n;
        emit = total >= 5'd16;
        last_word = pc == cfg.co - 11'd1 && pf == cfg.nt - 6'd1;
        for (int i = 0; i < 16; i++) word[i] = fifo[rp][8*i +: 8];
        // rot[p] = word[p - cnt mod 16]: word byte i lands at offset cnt+i.
        for (int p = 0; p < 16; p++) rot[p] = word[4'(p) - cnt];
        for (int p = 0; p < 16; p++) begin
            beat[p] = 4'(p) < cnt ? lo[p] : rot[p];
            if (emit) nlo[p] = 5'(p) < total - 5'd16 ? rot[p] : 8'd0;
            else nlo[p] = 4'(p) < cnt ? lo[p] : (5'(p) < total ? rot[p] : 8'd0);
        end
    end

    always_ff @(posedge clk) begin
        done <= 1'b0;
        if (rst) begin
            state <= K_IDLE;
            m_tvalid <= 1'b0;
            rv <= '0;
            fcount <= '0;
            reserved <= '0;
        end else begin
            if (m_tvalid && m_tready) begin
                m_tvalid <= 1'b0;
                if (m_tlast) begin
                    done <= 1'b1;
                    state <= K_IDLE;
                end
            end
            rv <= {rv[RD_LAT-2:0], re};
            reserved <= reserved + (FW+1)'(re) - (FW+1)'(pop);
            fcount <= fcount + (FW+1)'(push) - (FW+1)'(pop);
            if (push) begin
                fifo[wp] <= rdata;
                wp <= wp + 1'b1;
            end
            if (re) begin
                raddr <= raddr + 1'b1;
                if (rf == cfg.nt - 6'd1) begin
                    rf <= '0;
                    rc <= rc + 11'd1;
                end else rf <= rf + 6'd1;
            end
            case (state)
                K_IDLE: if (start) begin
                    state <= K_HDR;
                    hb <= '0;
                    rc <= '0;
                    rf <= '0;
                    pc <= '0;
                    pf <= '0;
                    cnt <= '0;
                    wp <= '0;
                    rp <= '0;
                    raddr <= '0;
                    for (int i = 0; i < 16; i++) lo[i] <= 8'd0;
                end
                K_HDR: if (slot) begin
                    m_tvalid <= 1'b1;
                    m_tdata <= header[4*hb +: 4];
                    m_tlast <= hb == 2'd3 && !with_output;
                    hb <= hb + 2'd1;
                    if (hb == 2'd3) state <= with_output ? K_DATA : K_WAIT;
                end
                K_DATA: if (pop) begin
                    rp <= rp + 1'b1;
                    if (pf == cfg.nt - 6'd1) begin
                        pf <= '0;
                        pc <= pc + 11'd1;
                    end else pf <= pf + 6'd1;
                    for (int i = 0; i < 16; i++) lo[i] <= nlo[i];
                    cnt <= emit ? 4'(total - 5'd16) : total[3:0];
                    if (emit) begin
                        m_tvalid <= 1'b1;
                        for (int i = 0; i < 16; i++) m_tdata[8*i +: 8] <= beat[i];
                        m_tlast <= last_word && total == 5'd16;
                    end
                    if (last_word) state <= (emit && total == 5'd16) ? K_WAIT : K_FLUSH;
                end
                K_FLUSH: if (slot) begin
                    m_tvalid <= 1'b1;
                    for (int i = 0; i < 16; i++) m_tdata[8*i +: 8] <= lo[i];
                    m_tlast <= 1'b1;
                    state <= K_WAIT;
                end
                default: ;
            endcase
        end
    end
endmodule
