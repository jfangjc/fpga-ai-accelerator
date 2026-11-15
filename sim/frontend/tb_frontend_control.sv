// Front end registers, FIFO, LED, button and pacing
module tb_frontend_control;
    `include "tb_frontend.svh"
    logic [31:0] value;
    int clock_count = 0, previous_accept = 0, accepted = 0;
    logic check_pace = 0, check_bulk = 0;

    assign sample = 16'sd1234;
    assign sample_valid = 1;
    assign features_ready = 0;

    always @(posedge clk) begin
        clock_count <= clock_count + 1;
        if (!resetn) begin
            accepted <= 0;
            previous_accept <= 0;
        end
        else if (sample_valid && sample_ready) begin
            if (accepted > 0 && check_pace && clock_count - previous_accept != 6250) begin
                $fatal(1, "FAIL 16 kHz pacing");
            end
            if (accepted > 0 && check_bulk && clock_count - previous_accept != 1) begin
                $fatal(1, "FAIL bulk pacing");
            end
            previous_accept <= clock_count;
            accepted <= accepted + 1;
        end
    end

    initial begin
        button = 0;
        #30 resetn = 1;
        write_reg(0, 32'h12345670);
        read_reg(0, value);
        if (value != 32'h12345670) begin
            $fatal(1, "FAIL register read back %h", value);
        end

        write_reg(0, 3);
        repeat (6000) @(negedge clk);
        if (sample_ready) begin
            $fatal(1, "FAIL FIFO backpressure");
        end

        // LED on; a 1 us button glitch is ignored, a held press is accepted once
        write_reg(0, 8);
        if (!led) begin
            $fatal(1, "FAIL LED");
        end
        button = 1;
        repeat (100) @(negedge clk);
        button = 0;
        repeat (100) @(negedge clk);
        read_reg(4, value);
        if (value[0]) begin
            $fatal(1, "FAIL button glitch accepted");
        end
        button = 1;
        repeat (1048600) @(negedge clk);
        read_reg(4, value);
        if (!value[0]) begin
            $fatal(1, "FAIL button press");
        end
        write_reg(4, 1);
        read_reg(4, value);
        if (value[0]) begin
            $fatal(1, "FAIL button not cleared");
        end
        repeat (200) @(negedge clk);
        read_reg(4, value);
        if (value[0]) begin
            $fatal(1, "FAIL held button pressed again");
        end
        write_reg(0, 0);
        if (led) begin
            $fatal(1, "FAIL LED off");
        end

        reset_dut();
        check_pace = 1;
        write_reg(0, 7);
        wait (accepted == 20);
        check_pace = 0;
        reset_dut();
        check_bulk = 1;
        write_reg(0, 3);
        wait (accepted == 20);
        check_bulk = 0;
        $display("PASS");
        $finish;
    end
endmodule
