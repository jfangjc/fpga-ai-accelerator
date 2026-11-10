// Activation unpacker: converts the contiguous channel-major [Ci][T] input
// byte stream (16 bytes per beat, final beat zero padded) into 16-frame
// activation words, one per (channel, frame tile), written to sequential
// bank addresses c*NT + f. Rows need not be multiples of 16 frames: each
// word takes 16 bytes, or last_n bytes for a row's final tile, from a
// two-beat window starting at byte offset off. Frames >= valid length
// (and therefore padding frames >= T) are written as zero, which implements
// quant_spec's "clear input positions beyond valid length".
// Throughput: one word per cycle, so input takes max(beats, Ci*NT) cycles.
module stt_act_unpack
    import stt_engine_pkg::*;
(
    input  logic              clk,
    input  logic              rst,
    input  logic              start,     // begin a tensor with cfg
    // Shared configuration struct; this module reads only some fields.
    /* verilator lint_off UNUSEDSIGNAL */
    input  layer_cfg_t        cfg,
    /* verilator lint_on UNUSEDSIGNAL */
    input  logic [127:0]      in_data,
    input  logic              in_valid,
    output logic              in_ready,
    output logic              we,
    output logic [ACT_AW-1:0] waddr,
    output logic [127:0]      wdata,
    output logic              done       // every word written
);
    logic [127:0] q [3];       // beat FIFO, q[0] is the window's low beat
    logic [1:0]   count;
    logic         active;
    logic         finish;
    logic [3:0]   off;         // first byte of the next word within q[0]
    logic [5:0]   f;           // frame tile of the next word
    logic [10:0]  c;           // channel of the next word
    logic [ACT_AW-1:0] addr;
    logic [4:0]   nbytes;
    logic [5:0]   off_next;
    logic         can_emit, pop, push, last_word;
    logic [1:0]   count_after_pop;
    logic [7:0]   win [32];
    logic [127:0] word;

    assign nbytes = (f == cfg.nt - 6'd1) ? cfg.last_n : 5'd16;
    assign off_next = {2'b0, off} + {1'b0, nbytes};
    // A word spanning two beats needs both in the FIFO.
    assign can_emit = active && count != 0 && (off_next <= 6'd16 || count >= 2);
    assign pop = can_emit && off_next >= 6'd16;
    assign in_ready = active && count != 2'd3;
    assign push = in_valid && in_ready;
    assign count_after_pop = count - 2'(pop);
    assign last_word = f == cfg.nt - 6'd1 && c == cfg.ci - 11'd1;

    always_comb begin
        for (int i = 0; i < 16; i++) begin
            win[i] = q[0][8*i +: 8];
            win[16+i] = q[1][8*i +: 8];
        end
        for (int i = 0; i < 16; i++) begin
            // Lane i is frame 16*f + i. Frames >= valid (<= T) read as zero.
            if ({f, 4'(i)} < cfg.valid) word[8*i +: 8] = win[5'(off) + 5'(i)];
            else word[8*i +: 8] = 8'd0;
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            active <= 1'b0;
            finish <= 1'b0;
            done <= 1'b0;
            count <= '0;
            we <= 1'b0;
        end else begin
            we <= 1'b0;
            done <= finish;
            if (start) begin
                active <= 1'b1;
                finish <= 1'b0;
                count <= '0;
                off <= '0;
                f <= '0;
                c <= '0;
                addr <= '0;
            end else if (active) begin
                if (pop) begin
                    q[0] <= q[1];
                    q[1] <= q[2];
                end
                if (push) q[count_after_pop] <= in_data;
                count <= count_after_pop + 2'(push);
                if (can_emit) begin
                    we <= 1'b1;
                    waddr <= addr;
                    wdata <= word;
                    addr <= addr + 1'b1;
                    off <= off_next[3:0];
                    if (f == cfg.nt - 6'd1) begin
                        f <= '0;
                        c <= c + 11'd1;
                    end else begin
                        f <= f + 6'd1;
                    end
                    // Any remaining FIFO data is the final beat's padding.
                    if (last_word) begin
                        active <= 1'b0;
                        finish <= 1'b1;
                    end
                end
            end
        end
    end
endmodule
