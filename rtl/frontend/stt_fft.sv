// Pre-emphasis, Hann window and 512-point FFT every 160 samples
module stt_fft
    import stt_tables_pkg::*;
(
    input logic clk, resetn,
    input logic signed [17:0] sample,
    input logic valid,
    output logic ready,
    output logic done,
    input logic [8:0] bin,
    output logic [63:0] bin_data,
    input logic next
);
    localparam int LATENCY = 1; // cycles from an operation to its product

    typedef enum logic [3:0] {COLLECT, READ, WINDOW, LOAD, FFT_READ, FFT_WAIT, MULTIPLY, FFT_WRITE, DONE} state_t;
    state_t state;

    (* rom_style = "block" *) logic signed [19:0] hann[0:319];
    (* rom_style = "block" *) logic signed [23:0] wr[0:255], wi[0:255];
    (* ram_style = "block" *) logic signed [31:0] history[0:511];
    (* ram_style = "block" *) logic [63:0] spectrum[0:511];
    initial begin
        hann = hann_table();
        wr = cos_table();
        wi = sin_table();
    end
    logic signed [31:0] history_q;
    logic signed [19:0] hann_q;
    logic signed [23:0] wr_q, wi_q;
    logic [63:0] q_a, q_b;

    logic [8:0] write_pointer, oldest, fill, n, base, j, half, address_a, address_b;
    logic [3:0] stage;
    logic [1:0] op;
    logic [2:0] wait_count;
    logic signed [17:0] previous;
    logic signed [31:0] ar, ai, br, bi, pre_value, window_value;
    logic signed [53:0] rr, ii, ri, ir, tr, ti; // bits 53:22 reach the 32-bit butterfly
    logic [63:0] data_a, data_b;
    logic write_a, write_b;
    logic signed [31:0] x, y, x_r, y_r;
    logic signed [63:0] xy;

    function automatic logic [8:0] reverse(input logic [8:0] x);
        for (int i = 0; i < 9; i++) reverse[i] = x[8 - i];
    endfunction

    assign ready = state == COLLECT;
    assign done = state == DONE;
    assign bin_data = q_a;
    assign pre_value = 32'(sample * 64'sd16 - (64'(previous * 25'sd16273900) >>> 20)); // x - 0.97 x', Q4
    assign window_value = 32'(64'(history_q) * hann_q >>> 18);

    // Butterfly a +- b * w
    assign half = 9'(1) << (stage - 1);
    assign tr = (rr - ii) >>> 22;
    assign ti = (ri + ir) >>> 22;
    always_comb begin
        address_a = base + j;
        address_b = base + j + half;
        write_a = state == FFT_WRITE;
        write_b = state == FFT_WRITE;
        data_a = {32'(ai + ti), 32'(ar + tr)};
        data_b = {32'(ai - ti), 32'(ar - tr)};
        if (state == LOAD) begin
            address_a = reverse(n);
            write_a = 1;
            write_b = 0;
            data_a = n < 320 ? {32'b0, window_value} : 0;
        end
        if (state == DONE) begin
            address_a = bin;
        end
    end

    always_comb
        case (op)
            0: {x, y} = {br, 32'(wr_q)};
            1: {x, y} = {bi, 32'(wi_q)};
            2: {x, y} = {br, 32'(wi_q)};
            default: {x, y} = {bi, 32'(wr_q)};
        endcase
    assign xy = x_r * y_r;

    always_ff @(posedge clk) begin
        if (state == COLLECT && valid) begin
            history[write_pointer] <= pre_value;
        end
        history_q <= history[oldest + n];
        hann_q <= hann[n < 320 ? n : 0];
        wr_q <= wr[8'(j << (9 - stage))];
        wi_q <= wi[8'(j << (9 - stage))];
        x_r <= x;
        y_r <= y;
    end
    always_ff @(posedge clk) begin
        if (write_a) begin
            spectrum[address_a] <= data_a;
        end
        q_a <= spectrum[address_a];
    end
    always_ff @(posedge clk) begin
        if (write_b) begin
            spectrum[address_b] <= data_b;
        end
        q_b <= spectrum[address_b];
    end

    always_ff @(posedge clk) begin
        if (!resetn) begin
            state <= COLLECT;
            write_pointer <= 0;
            oldest <= 0;
            fill <= 0;
            previous <= 0;
            n <= 0;
            base <= 0;
            j <= 0;
            stage <= 1;
            op <= 0;
            wait_count <= 0;
        end
        else begin
            case (state)
                // 320 samples for the first frame, 160 after
                COLLECT:
                if (valid) begin
                    previous <= sample;
                    write_pointer <= write_pointer + 1;
                    if (fill == 319) begin
                        n <= 0;
                        state <= READ;
                    end
                    else begin
                        fill <= fill + 1;
                    end
                end
                // Windowed samples in bit-reversed order
                READ: state <= WINDOW;
                WINDOW: state <= LOAD;
                LOAD:
                if (n == 511) begin
                    stage <= 1;
                    base <= 0;
                    j <= 0;
                    state <= FFT_READ;
                end
                else begin
                    n <= n + 1;
                    state <= READ;
                end
                FFT_READ: state <= FFT_WAIT;
                FFT_WAIT: begin
                    {ai, ar} <= q_a;
                    {bi, br} <= q_b;
                    op <= 0;
                    state <= MULTIPLY;
                end
                MULTIPLY:
                if (wait_count != LATENCY) begin
                    wait_count <= wait_count + 1;
                end
                else begin
                    wait_count <= 0;
                    op <= op + 1;
                    case (op)
                        0: rr <= 54'(xy);
                        1: ii <= 54'(xy);
                        2: ri <= 54'(xy);
                        default: begin
                            ir <= 54'(xy);
                            state <= FFT_WRITE;
                        end
                    endcase
                end
                FFT_WRITE: begin
                    state <= FFT_READ;
                    if (j != half - 1) begin
                        j <= j + 1;
                    end
                    else begin
                        j <= 0;
                        if (10'(base) + 10'(half) * 2 != 512) begin
                            base <= base + half * 2;
                        end
                        else begin
                            base <= 0;
                            if (stage != 9) begin
                                stage <= stage + 1;
                            end
                            else begin
                                state <= DONE;
                            end
                        end
                    end
                end
                // Hold the spectrum for stt_mel
                DONE:
                if (next) begin
                    oldest <= oldest + 160;
                    fill <= 160;
                    state <= COLLECT;
                end
                default: state <= COLLECT;
            endcase
        end
    end
endmodule
