// Standalone activation-bank packing regression. Run with test_act_unpack.sh.
module tb_act_unpack;
    import stt_engine_pkg::*;
    logic clk;
    always begin clk = 0; #5; clk = 1; #5; end
    logic rst, start, in_valid, in_ready, we, done;
    logic [127:0] in_data, wdata;
    logic [ACT_AW-1:0] waddr;
    layer_cfg_t cfg;
    stt_act_unpack dut (.*);
    int checked = 0;
    int cases = 0;
    int unsigned rng = 32'h1729;
    function automatic int unsigned random_word();
        rng ^= rng << 13;
        rng ^= rng >> 17;
        rng ^= rng << 5;
        return rng;
    endfunction
    function automatic byte sample(input int address);
        return 8'((address * 37) ^ (address >> 5) ^ 173);
    endfunction
    task automatic run_case(input int ci, t, valid_frames);
        int sent, received, beats, words, frame, channel, offset;
        bit holding;
        @(negedge clk);
        cfg = '0;
        cfg.ci = 11'(ci);
        cfg.t = 10'(t);
        cfg.valid = 10'(valid_frames);
        cfg.nt = 6'((t + 15)/16);
        cfg.last_n = 5'((t-1)%16+1);
        start = 1;
        in_valid = 0;
        @(negedge clk);
        start = 0;
        sent = 0;
        received = 0;
        beats = (ci*t+15)/16;
        words = ci*((t+15)/16);
        holding = 0;
        for (int cycle = 0; cycle < 4*words+4*beats+100; cycle++) begin
            if (!holding) begin
                in_valid = sent < beats && (random_word()%4 != 0);
                for (int lane = 0; lane < 16; lane++)
                    in_data[8*lane +: 8] = sent*16+lane < ci*t ? sample(sent*16+lane) : 8'hde;
            end
            holding = in_valid && !in_ready;
            if (in_valid && in_ready) sent++;
            @(posedge clk);
            #1;
            if (we) begin
                if (int'(waddr) != received) $fatal(1, "address got %0d expected %0d", waddr, received);
                channel = received/((t+15)/16);
                frame = 16*(received%((t+15)/16));
                for (int lane = 0; lane < 16; lane++) begin
                    offset = channel*t+frame+lane;
                    if (wdata[8*lane +: 8] !== (frame+lane < valid_frames ? sample(offset) : 8'd0))
                        $fatal(1, "packing ci=%0d t=%0d valid=%0d channel=%0d frame=%0d", ci,t,valid_frames,channel,frame+lane);
                    checked++;
                end
                received++;
            end
            if (done) begin
                if (sent != beats || received != words) $fatal(1, "incomplete transfer");
                cases++;
                break;
            end
            if (cycle == 4*words+4*beats+99) $fatal(1, "timeout");
            @(negedge clk);
        end
        @(negedge clk);
        in_valid = 0;
    endtask
    initial begin
        rst = 1;
        start = 0;
        in_valid = 0;
        in_data = 0;
        cfg = '0;
        repeat (3) @(negedge clk);
        rst = 0;
        for (int t = 1; t <= 49; t++) begin
            run_case(19, t, t);
            run_case(3, t, t/2);
            run_case(1, t, 0);
        end
        run_case(1024, 800, 799);
        run_case(1024, 17, 16);
        run_case(29, 150, 137);
        $display("PASS activation unpack: %0d cases, %0d bytes checked", cases, checked);
        $finish;
    end
endmodule

