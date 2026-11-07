// Engine top level running a four-layer program with simple weights
module tb_engine;
logic clk = 0, resetn = 0;
always #5 clk = ~clk;
logic [127:0] s_data = 0, m_data;
logic s_valid = 0, s_last = 0, s_ready, m_valid, m_last, m_ready = 1;

stt_engine dut(clk, resetn, s_data, s_valid, s_last, s_ready, m_data, m_valid, m_last, m_ready);

byte unsigned command[$], reply[$];
int x[16][64], y1[16][64], y2[16][64], y3[16][64];

function automatic void put32(int value);
    for (int i = 0; i < 4; i++) begin
        command.push_back(value >> 8 * i);
    end
endfunction

// A record for 16 output channels, then the bias (convolutions only), multiplier 2^30 and shift -1 per channel
function automatic void layer(int kind, relu, source, branch, dest, taps, stride, dilation, padding, ci);
    byte unsigned record[16] = '{
        kind, relu, source, branch, dest, taps, stride, dilation, padding, 0, 0, 0, 16, 0, ci, 0};
    foreach (record[i]) begin
        command.push_back(record[i]);
    end
    for (int a = 0; a < (kind == 2 ? 4 : 3); a++) begin
        for (int o = 0; o < 16; o++) begin
            if (kind == 2) begin
                put32(a % 2 ? -1 : 32'h40000000);
            end
            else begin
                put32(a == 0 ? 0 : a == 1 ? 32'h40000000 : -1);
            end
        end
    end
endfunction

// One tile of 16 weight rows, 2 on the diagonal for pointwise or 1 on the first three rows for depthwise
function automatic void weights(bit pointwise);
    for (int j = 0; j < 16; j++) begin
        for (int l = 0; l < 32; l++) begin
            command.push_back(pointwise ? 2 * (l == j) : l < 16 && j < 3);
        end
    end
endfunction

// Reference depthwise of 3 taps with weights 1 over the first valid frames
function automatic int depthwise(ref int a[16][64], input int c, t, stride, dilation, padding, valid);
    int sum = 0;
    for (int k = 0; k < 3; k++) begin
        int p = t * stride + k * dilation - padding;
        if (p >= 0 && p < valid) begin
            sum += a[c][p];
        end
    end
    return sum;
endfunction

initial begin
    int sent = 0;
    put32(16);
    put32(37);
    put32(30);
    put32(0);
    for (int c = 0; c < 16; c++) begin
        for (int t = 0; t < 48; t++) begin
            x[c][t] = t < 37 ? (c + 3 * t) % 11 - 5 : 0;
            command.push_back(x[c][t]);
        end
    end
    // Arguments are kind, relu, source bank, branch bank, dest bank, taps, stride, dilation, padding, ci
    // y1 in bank 1 is a depthwise of x with 3 taps, stride 2 and padding 1
    layer(1, 0, 0, 0, 1, 3, 2, 1, 1, 1);
    weights(0);
    // y2 in bank 2 is a pointwise of y1 that doubles each channel
    layer(0, 0, 1, 0, 2, 1, 1, 1, 0, 16);
    weights(1);
    // y3 in bank 0 is a depthwise of y2 with 3 taps, dilation 2 and padding 2
    layer(1, 0, 2, 0, 0, 3, 1, 2, 2, 1);
    weights(0);
    // The output in bank 1 is y2 + y3 clamped to UINT8
    layer(2, 1, 2, 0, 1, 0, 0, 0, 0, 16);

    // 37 frames with 30 valid become 19 frames with 15 valid after stride 2
    for (int c = 0; c < 16; c++) begin
        for (int t = 0; t < 19; t++) begin
            y1[c][t] = depthwise(x, c, t, 2, 1, 1, 30);
            y2[c][t] = t < 15 ? 2 * y1[c][t] : 0;
        end
        for (int t = 0; t < 19; t++) begin
            y3[c][t] = depthwise(y2, c, t, 1, 2, 2, 15);
        end
    end

    // Send the command and collect the reply
    repeat (3) @(negedge clk);
    resetn = 1;
    while (!(m_valid && m_last)) begin
        @(negedge clk);
        s_valid = sent < command.size();
        s_last = sent + 16 == command.size();
        for (int i = 0; i < 16; i++) begin
            s_data[8 * i+:8] = sent + i < command.size() ? command[sent + i] : 0;
        end
        #1;
        if (s_valid && s_ready) begin
            sent += 16;
        end
        if (m_valid) begin
            for (int i = 0; i < 16; i++) begin
                reply.push_back(m_data[8 * i+:8]);
            end
        end
    end

    if (reply.size() != 16 + 16 * 2 * 16 || reply[0] !== 19 || reply[4] !== 15) begin
        $fatal(1, "FAIL reply header");
    end
    for (int c = 0; c < 16; c++) begin
        for (int t = 0; t < 19; t++) begin
            int sum = y2[c][t] + y3[c][t];
            if (reply[16 + 32 * c + t] !== (sum < 0 ? 0 : sum > 255 ? 255 : sum)) begin
                $fatal(1, "FAIL channel %0d frame %0d", c, t);
            end
        end
    end
    $display("PASS");
    $finish;
end
endmodule
