// Area-oriented streaming FFT/mel scheduler. All FFT and history reads are
// registered. The exact legacy arithmetic is retained; a shared partial-product
// multiplier trades throughput for area (well below the 10 ms frame budget).
module stt_fft_mel (
    input logic clk, resetn,
    input logic signed [17:0] sample,
    input logic valid,
    output logic ready,
    output logic [31:0] data,
    output logic out_valid,
    input logic out_ready,
    output logic last, frame_done
);
    typedef enum logic [4:0] {
        COLLECT, HISTORY_READ, WINDOW, LOAD, FFT_READ, FFT_WAIT,
        MULT_START, MULT_WAIT, FFT_WRITE, POWER_READ, POWER_WAIT,
        CLEAR_BANDS, MEL_READ, MEL_WAIT, OUTPUT_READ, OUTPUT_FRAME
    } state_t;
    state_t state;
    logic signed [31:0] history_q;
    (* ram_style = "block" *) logic signed [31:0] history [0:511];
    (* rom_style = "block" *) logic signed [19:0] hann [0:159];
    (* rom_style = "block" *) logic signed [23:0] wr [0:255], wi [0:255];
    (* rom_style = "block" *) logic [61:0] mel [0:256];
    logic signed [19:0] hann_q;
    logic signed [23:0] wr_q, wi_q;
    logic [61:0] mel_q;
    (* ram_style = "block" *) logic [95:0] bands [0:63];
    logic [95:0] band_q;
    logic [5:0] band_address;
    logic [8:0] write_pointer, oldest, fill, n;
    logic [8:0] half_size, base, j, twiddle_step, twiddle_index;
    logic [3:0] stage;
    logic [5:0] clear_band;
    logic [7:0] word_number;
    logic [31:0] sequence_number;
    logic signed [17:0] previous;
    logic signed [45:0] pre_product;
    logic signed [31:0] pre_value, window_value;
    logic [8:0] address_a, address_b;
    logic write_a, write_b;
    logic [63:0] data_a, data_b, q_a, q_b;
    logic signed [31:0] ar, ai, br, bi;
    logic signed [63:0] rr, ri, ir, ii, tr, ti;
    logic [63:0] power_value;
    logic [2:0] operation;
    logic negative;
    logic [63:0] mul_a;
    logic [31:0] mul_b;
    logic [95:0] mul_result;
    logic mul_done;
    logic signed [63:0] signed_product;
    logic [63:0] output_band;

    initial begin
        $readmemh("hann_half.mem", hann);
        $readmemh("tw_re.mem", wr);
        $readmemh("tw_im.mem", wi);
        $readmemh("mel_sparse.mem", mel);
    end
    function automatic logic [8:0] reverse_bits(input logic [8:0] value);
        for (int bit_index = 0; bit_index < 9; bit_index++)
            reverse_bits[bit_index] = value[8-bit_index];
    endfunction
    function automatic logic [31:0] magnitude(input logic signed [31:0] value);
        magnitude = value[31] ? $unsigned(-value) : $unsigned(value);
    endfunction
    assign pre_product = previous * 28'sd130191200;
    assign pre_value = 32'(($signed(sample) * 64'sd16) - (64'($signed(pre_product)) >>> 23));
    assign window_value = 32'((64'($signed(history_q)) * $signed(hann_q)) >>> 18);
    assign ready = state == COLLECT;
    assign out_valid = state == OUTPUT_FRAME;
    assign last = word_number == 129;
    assign band_address = (state == OUTPUT_READ || state == OUTPUT_FRAME) ?
                          6'((word_number-8'd2)>>1) :
                          mel_q[61:56] + (operation == 7 ? 6'd1 : 6'd0);
    assign output_band = band_q[91:28];
    assign data = word_number == 0 ? sequence_number :
                  word_number == 1 ? 32'hffffffd6 :
                  word_number[0] ? output_band[63:32] : output_band[31:0];
    assign tr = (rr - ii) >>> 22;
    assign ti = (ri + ir) >>> 22;
    assign signed_product = negative ? -$signed(mul_result[63:0]) : $signed(mul_result[63:0]);

    // The RAM ports are never used by more than one client at a time.
    always_comb begin
        address_a = base + j;
        address_b = base + j + half_size;
        write_a = state == FFT_WRITE;
        write_b = state == FFT_WRITE;
        data_a = {32'(ai+ti),32'(ar+tr)};
        data_b = {32'(ai-ti),32'(ar-tr)};
        if (state == LOAD) begin
            address_a = reverse_bits(n);
            write_a = 1'b1;
            write_b = 1'b0;
            data_a = n < 320 ? {32'b0,window_value[31:0]} : 64'b0;
        end
        if (state == POWER_READ || state == POWER_WAIT) address_a = n;
        mul_a = 0;
        mul_b = 0;
        case (operation)
            0: begin mul_a = {32'b0,magnitude(br)}; mul_b = magnitude({{8{wr_q[23]}},wr_q}); end
            1: begin mul_a = {32'b0,magnitude(bi)}; mul_b = magnitude({{8{wi_q[23]}},wi_q}); end
            2: begin mul_a = {32'b0,magnitude(br)}; mul_b = magnitude({{8{wi_q[23]}},wi_q}); end
            3: begin mul_a = {32'b0,magnitude(bi)}; mul_b = magnitude({{8{wr_q[23]}},wr_q}); end
            4: begin mul_a = {32'b0,magnitude(ar)}; mul_b = magnitude(ar); end
            5: begin mul_a = {32'b0,magnitude(ai)}; mul_b = magnitude(ai); end
            6: begin mul_a = power_value; mul_b = {4'b0,mel_q[27:0]}; end
            7: begin mul_a = power_value; mul_b = {4'b0,mel_q[55:28]}; end
            default: ;
        endcase
    end
    stt_fft_ram storage(clk,address_a,address_b,write_a,write_b,data_a,data_b,q_a,q_b);
    stt_serial_mul multiplier(clk,resetn,state==MULT_START,mul_a,mul_b,mul_result,mul_done);

    // Synchronous ROM/history reads; the following state consumes each result.
    always_ff @(posedge clk) begin
        if (state == COLLECT && valid) history[write_pointer] <= pre_value[31:0];
        history_q <= history[(oldest+n) & 9'h1ff];
        hann_q <= hann[n < 160 ? n[7:0] : 8'(319-n)];
        wr_q <= wr[twiddle_index[7:0]];
        wi_q <= wi[twiddle_index[7:0]];
        mel_q <= mel[n];
        band_q <= bands[band_address];
    end
    always_ff @(posedge clk) begin
        frame_done <= 1'b0;
        if (!resetn) begin
            state <= COLLECT;
            write_pointer <= 0;
            oldest <= 0;
            fill <= 0;
            previous <= 0;
            sequence_number <= 0;
            n <= 0;
            word_number <= 0;
            base <= 0;
            j <= 0;
            half_size <= 1;
            stage <= 1;
            twiddle_index <= 0;
            twiddle_step <= 256;
            operation <= 0;
            negative <= 0;
            clear_band <= 0;
        end else case (state)
            COLLECT: if (valid) begin
                previous <= sample;
                write_pointer <= write_pointer + 1'b1;
                if (fill == 319) begin n <= 0; state <= HISTORY_READ; end
                else fill <= fill + 1'b1;
            end
            HISTORY_READ: state <= WINDOW;
            WINDOW: state <= LOAD;
            LOAD: begin
                if (n == 511) begin
                    stage <= 1; half_size <= 1; base <= 0; j <= 0;
                    twiddle_index <= 0; twiddle_step <= 256; state <= FFT_READ;
                end else begin n <= n + 1'b1; state <= HISTORY_READ; end
            end
            FFT_READ: state <= FFT_WAIT;
            FFT_WAIT: begin
                ar <= $signed(q_a[31:0]); ai <= $signed(q_a[63:32]);
                br <= $signed(q_b[31:0]); bi <= $signed(q_b[63:32]);
                operation <= 0; state <= MULT_START;
            end
            MULT_START: begin
                case (operation)
                    0: negative <= br[31] ^ wr_q[23];
                    1: negative <= bi[31] ^ wi_q[23];
                    2: negative <= br[31] ^ wi_q[23];
                    3: negative <= bi[31] ^ wr_q[23];
                    default: negative <= 0;
                endcase
                state <= MULT_WAIT;
            end
            MULT_WAIT: if (mul_done) begin
                state <= MULT_START;
                operation <= operation + 1'b1;
                case (operation)
                    0: rr <= signed_product;
                    1: ii <= signed_product;
                    2: ri <= signed_product;
                    3: begin ir <= signed_product; state <= FFT_WRITE; end
                    4: power_value <= mul_result[63:0];
                    5: begin power_value <= power_value + mul_result[63:0]; state <= MEL_READ; end
                    6: bands[mel_q[61:56]] <= band_q + mul_result;
                    7: begin
                        // At the top edge only one filter exists; its high weight is zero.
                        if (mel_q[61:56] != 63)
                            bands[mel_q[61:56]+6'd1] <= band_q + mul_result;
                        if (n == 256) begin word_number <= 0; state <= OUTPUT_READ; end
                        else begin n <= n + 1'b1; state <= POWER_READ; end
                    end
                    default: ;
                endcase
            end
            FFT_WRITE: begin
                state <= FFT_READ;
                if (j == half_size-1'b1) begin
                    j <= 0; twiddle_index <= 0;
                    if ({1'b0,base}+{half_size,1'b0} == 512) begin
                        base <= 0;
                        if (stage == 9) begin clear_band <= 0; state <= CLEAR_BANDS; end
                        else begin stage <= stage+1'b1; half_size <= half_size<<1; twiddle_step <= twiddle_step>>1; end
                    end else base <= base + (half_size<<1);
                end else begin j <= j+1'b1; twiddle_index <= twiddle_index+twiddle_step; end
            end
            CLEAR_BANDS: begin
                bands[clear_band] <= 0;
                if (clear_band == 63) begin n <= 0; state <= POWER_READ; end
                else clear_band <= clear_band+1'b1;
            end
            POWER_READ: state <= POWER_WAIT;
            POWER_WAIT: begin
                ar <= $signed(q_a[31:0]); ai <= $signed(q_a[63:32]);
                operation <= 4; state <= MULT_START;
            end
            MEL_READ: state <= MEL_WAIT;
            MEL_WAIT: begin operation <= 6; state <= MULT_START; end
            OUTPUT_READ: state <= OUTPUT_FRAME;
            OUTPUT_FRAME: if (out_ready) begin
                if (word_number == 129) begin
                    frame_done <= 1'b1; sequence_number <= sequence_number+1'b1;
                    oldest <= oldest+9'd160; fill <= 160; state <= COLLECT;
                end else begin word_number <= word_number+1'b1; state <= OUTPUT_READ; end
            end
            default: state <= COLLECT;
        endcase
    end
endmodule
