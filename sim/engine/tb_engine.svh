// Engine testbench harness and integer reference
logic clk = 0, resetn = 0;
always #5 clk = ~clk;
logic [127:0] s_data = 0, m_data;
logic s_valid = 0, s_last = 0, s_ready, m_valid, m_last, m_ready = 0;

stt_engine dut(clk, resetn, s_data, s_valid, s_last, s_ready, m_data, m_valid, m_last, m_ready);

// A layer of tensor a (and b) into a bank; tensor 0 is the input, tensor i layer i's output
typedef struct {
    int kind, relu, a, b, bank, co, taps, stride, dilation, padding;
} spec_t;
typedef byte unsigned bytes_t[$];

int tensor[0:8][0:63][0:63], channels[0:8], frames[0:8], valid[0:8], bank[0:8];

function automatic spec_t pointwise(int a, int co, int relu, int bank);
    return '{0, relu, a, 0, bank, co, 1, 1, 1, 0};
endfunction

function automatic spec_t depthwise(int a, int taps, int stride, int dilation, int padding, int bank);
    return '{1, 0, a, 0, bank, 0, taps, stride, dilation, padding};
endfunction

function automatic spec_t add(int a, int b, int bank);
    return '{2, 1, a, b, bank, 0, 0, 0, 0, 0};
endfunction

function automatic void put32(ref bytes_t v, input int x);
    for (int i = 0; i < 4; i++) v.push_back(x >> 8 * i);
endfunction

function automatic void pad16(ref bytes_t v);
    while (v.size() % 16) v.push_back(0);
endfunction

function automatic int get32(ref bytes_t v, input int p);
    return {v[p+3], v[p+2], v[p+1], v[p]};
endfunction

function automatic int floor_div(int a, int b);
    return a >= 0 ? a / b : -((b - 1 - a) / b);
endfunction

// Reference requantiser
function automatic int requant(longint acc, int mult, int shift, int lo, int hi);
    longint p = acc * mult, q;
    int n = 31 + shift;
    q = n >= 64 ? 0 : ((p < 0 ? -p : p) + (64'sd1 <<< (n - 1))) >>> n;
    if (p < 0) begin
        q = -q;
    end
    return q < lo ? lo : q > hi ? hi : int'(q);
endfunction

// Layer i: random parameters, its program bytes and its reference output
function automatic void layer(int i, spec_t s, ref bytes_t command);
    int a = s.a, b = s.b, ci = channels[s.a], t = frames[s.a], v = valid[s.a];
    int co = s.kind == 0 ? s.co : ci, k = s.kind == 1 ? s.taps : ci, ci_word = s.kind == 1 ? 1 : ci;
    int w[64][64], bias[64], mult[2][64], shift[2][64];
    byte unsigned record[16];
    for (int o = 0; o < co; o++) begin
        bias[o] = $urandom_range(40000) - 20000;
        for (int j = 0; j < 2; j++) begin
            mult[j][o] = 32'h40000000 | $urandom_range(32'h3fffffff);
            shift[j][o] = s.kind == 2 ? $urandom_range(2) - 1 : $urandom_range(9, 6);
        end
        for (int j = 0; j < k; j++) w[o][j] = $urandom_range(254) - 127;
    end
    channels[i] = co;
    bank[i] = s.bank;
    frames[i] = t;
    valid[i] = v;
    if (s.kind == 1) begin
        frames[i] = floor_div(t + 2 * s.padding - s.dilation * (s.taps - 1) - 1, s.stride) + 1;
        valid[i] = floor_div(v + 2 * s.padding - s.dilation * (s.taps - 1) - 1, s.stride) + 1;
        if (valid[i] < 0) begin
            valid[i] = 0;
        end
    end
    for (int o = 0; o < co; o++)
        for (int j = 0; j < frames[i]; j++) begin
            longint acc = bias[o];
            if (s.kind == 0) begin
                for (int c = 0; c < ci; c++) acc += (j < v ? tensor[a][c][j] : 0) * w[o][c];
            end
            if (s.kind == 1) begin
                for (int tap = 0; tap < s.taps; tap++) begin
                    int p = j * s.stride + tap * s.dilation - s.padding;
                    if (p >= 0 && p < v) begin
                        acc += tensor[a][o][p] * w[o][tap];
                    end
                end
            end
            if (s.kind == 2) begin
                int sum = requant(tensor[a][o][j], mult[0][o], shift[0][o], -32768, 32767)
                        + requant(tensor[b][o][j], mult[1][o], shift[1][o], -32768, 32767);
                tensor[i][o][j] = sum < 0 ? 0 : sum > 255 ? 255 : sum;
            end
            else begin
                tensor[i][o][j] = s.relu ? requant(acc, mult[0][o], shift[0][o], 0, 255)
                                         : requant(acc, mult[0][o], shift[0][o], -128, 127);
            end
        end

    record = '{s.kind, s.relu, bank[a], bank[b], s.bank, s.taps, s.stride, s.dilation, s.padding, 0, 0, 0, co,
               co >> 8, ci_word, ci_word >> 8};
    foreach (record[j]) command.push_back(record[j]);
    if (s.kind == 2) begin
        for (int j = 0; j < 2; j++) begin
            for (int o = 0; o < co; o++) put32(command, mult[j][o]);
            pad16(command);
            for (int o = 0; o < co; o++) put32(command, shift[j][o]);
            pad16(command);
        end
    end
    else begin
        for (int o = 0; o < co; o++) put32(command, bias[o]);
        pad16(command);
        for (int o = 0; o < co; o++) put32(command, mult[0][o]);
        pad16(command);
        for (int o = 0; o < co; o++) put32(command, shift[0][o]);
        pad16(command);
        for (int tile = 0; tile < (co + 31) / 32; tile++)
            for (int j = 0; j < (k + 15) / 16 * 16; j++)
                for (int l = 0; l < 32; l++)
                    command.push_back(32 * tile + l < co && j < k ? w[32*tile+l][j] : 0);
    end
endfunction

// Send a command and collect the reply, with random stalls on both streams
task automatic transact(ref bytes_t command, ref bytes_t reply);
    int sent = 0, cycles = 0;
    bit hold = 0, stall = 0, done = 0;
    logic [127:0] held;
    reply.delete();
    while (!done) begin
        @(negedge clk);
        if (!hold) begin
            s_valid = sent < command.size() && $urandom_range(4) != 0;
            for (int i = 0; i < 16; i++) s_data[8*i+:8] = sent + i < command.size() ? command[sent+i] : 0;
            s_last = sent + 16 == command.size();
        end
        m_ready = $urandom_range(3) != 0;
        #1;
        if (stall && (!m_valid || m_data !== held)) begin
            $fatal(1, "FAIL stalled reply changed");
        end
        stall = m_valid && !m_ready;
        held = m_data;
        hold = s_valid && !s_ready;
        if (s_valid && s_ready) begin
            sent += 16;
        end
        if (m_valid && m_ready) begin
            for (int i = 0; i < 16; i++) reply.push_back(m_data[8*i+:8]);
            done = m_last;
        end
        if (++cycles == 2000000) begin
            $fatal(1, "FAIL no reply");
        end
    end
    @(negedge clk);
    s_valid = 0;
    m_ready = 0;
    if (sent != command.size()) begin
        $fatal(1, "FAIL early reply");
    end
endtask

// Run a network on a random input and check the reply
task automatic run(string name, int inputs, int t, int v, spec_t specs[$]);
    bytes_t command, reply;
    int n = specs.size(), out_tiles;
    channels[0] = inputs;
    frames[0] = t;
    valid[0] = v;
    bank[0] = 0;
    put32(command, inputs);
    put32(command, t);
    put32(command, v);
    put32(command, 0);
    for (int c = 0; c < inputs; c++)
        for (int j = 0; j < (t + 15) / 16 * 16; j++) begin
            tensor[0][c][j] = j < t ? $urandom_range(255) - 128 : 0;
            command.push_back(tensor[0][c][j]);
        end
    foreach (specs[i]) layer(i + 1, specs[i], command);
    pad16(command);
    transact(command, reply);
    out_tiles = (frames[n] + 15) / 16;
    if (get32(reply, 0) != frames[n] || get32(reply, 4) != valid[n]) begin
        $fatal(1, "FAIL %s: reply header", name);
    end
    if (reply.size() != 16 + 16 * channels[n] * out_tiles) begin
        $fatal(1, "FAIL %s: reply size", name);
    end
    for (int c = 0; c < channels[n]; c++)
        for (int j = 0; j < frames[n]; j++)
            if (reply[16 + 16 * out_tiles * c + j] !== 8'(tensor[n][c][j])) begin
                $fatal(1, "FAIL %s: channel %0d frame %0d", name, c, j);
            end
endtask

task automatic reset_dut();
    @(negedge clk);
    resetn = 0;
    repeat (3) @(negedge clk);
    resetn = 1;
endtask
