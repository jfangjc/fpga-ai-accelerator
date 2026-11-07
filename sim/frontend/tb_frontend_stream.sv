// Front end features of silence then a 1 kHz tone
module tb_frontend_stream;
    `include "tb_frontend.svh"
    localparam int FRAMES = 10, SAMPLES = 160 * (FRAMES - 1) + 320;
    localparam logic [63:0] PEAK = 64'd12987532282305;
    logic [31:0] rng = 32'h1234567, low;
    logic [63:0] band[FRAMES][64];
    int sent = 0, words = 0, cycles = 0;

    function automatic logic signed [15:0] stimulus(int n);
        if (n < 960) begin
            return 0;
        end
        return 16'(longint'($rtoi($floor(12500.0 * $sin(3.14159265358979 * n / 8.0) + 0.5))));
    endfunction

    assign button = 0;
    assign sample = stimulus(sent);
    assign features_ready = rng[3];
    always @(posedge clk) rng <= (rng >> 1) ^ ((32'd0 - (rng & 1)) & 32'hd0000001);
    always @(negedge clk)
        if (!resetn) begin
            sample_valid <= 0;
        end
        else if (!sample_valid || sample_ready) begin
            sample_valid <= sent < SAMPLES;
        end

    always @(posedge clk)
        if (resetn) begin
            cycles <= cycles + 1;
            if (cycles > 5_000_000) begin
                $fatal(1, "FAIL timeout after %0d words", words);
            end
            if (sample_valid && sample_ready) begin
                sent <= sent + 1;
            end
            if (features_valid && features_ready) begin
                automatic int f = words / 128, w = words % 128;
                if (w % 2 == 0) begin
                    low <= features;
                end
                else begin
                    band[f][w / 2] = {features, low};
                end
                if (features_last != (w == 127)) begin
                    $fatal(1, "FAIL TLAST at word %0d", words);
                end
                words <= words + 1;
            end
        end

    initial begin
        repeat (3) @(negedge clk);
        resetn = 1;
        write_reg(0, 3); // enable, injection, bulk
        wait (words == 128 * FRAMES);
        for (int f = 0; f < 5; f++)
            for (int b = 0; b < 64; b++)
                if (band[f][b] != 0) begin
                    $fatal(1, "FAIL silent frame %0d band %0d = %0d", f, b, band[f][b]);
                end
        for (int f = 6; f < FRAMES; f++)
            for (int b = 0; b < 64; b++)
                if (band[f][b] != band[6][b]) begin
                    $fatal(1, "FAIL tone frame %0d differs at band %0d", f, b);
                end
        for (int b = 0; b < 64; b++)
            if (b != 20 && band[6][b] >= band[6][20]) begin
                $fatal(1, "FAIL tone peak not in band 20 (band %0d)", b);
            end
        if (band[6][20] != PEAK) begin
            $fatal(1, "FAIL band 20 = %0d, expected %0d", band[6][20], PEAK);
        end
        $display("PASS");
        $finish;
    end
endmodule
