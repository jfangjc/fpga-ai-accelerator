// Audio frontendS
module stt_frontend (
    input logic aclk, aresetn,
    input logic [11:0] s_axi_awaddr,
    input logic s_axi_awvalid,
    output logic s_axi_awready,
    input logic [31:0] s_axi_wdata,
    input logic s_axi_wvalid,
    output logic s_axi_wready,
    output logic s_axi_bvalid,
    input logic s_axi_bready,
    input logic [11:0] s_axi_araddr,
    input logic s_axi_arvalid,
    output logic s_axi_arready,
    output logic [31:0] s_axi_rdata,
    output logic s_axi_rvalid,
    input logic s_axi_rready,
    input logic [15:0] s_axis_samples_tdata,
    input logic s_axis_samples_tvalid,
    output logic s_axis_samples_tready,
    output logic [31:0] m_axis_features_tdata,
    output logic m_axis_features_tlast, m_axis_features_tvalid,
    input logic m_axis_features_tready,
    output logic pmod_i2s_bclk, pmod_i2s_lrclk,
    input logic pmod_i2s_dout,
    output logic pmod_led_d1,
    input logic pmod_btn_sw1
);
    logic [31:0] control;
    logic button, pressed;
    logic [1:0] button_sync;
    logic [19:0] debounce;
    logic signed [17:0] mic, selected;
    logic mic_valid, inject, push, pop, fft_ready, spectrum_done, spectrum_next;
    logic [8:0] bin;
    logic [63:0] bin_data;
    (* ram_style = "block" *) logic signed [17:0] fifo[0:1023];
    logic signed [17:0] fifo_q;
    logic fifo_q_valid;
    logic [9:0] write_pointer, read_pointer;
    logic [10:0] count;
    logic [12:0] pace;

    stt_i2s i2s(aclk, aresetn, pmod_i2s_dout, pmod_i2s_bclk, pmod_i2s_lrclk, mic, mic_valid);
    stt_fft fft(aclk, aresetn, fifo_q, pop, fft_ready, spectrum_done, bin, bin_data, spectrum_next);
    stt_mel mel(aclk, aresetn, spectrum_done, bin, bin_data, spectrum_next, m_axis_features_tdata,
                m_axis_features_tvalid, m_axis_features_tlast, m_axis_features_tready);

    assign pmod_led_d1 = control[3];

    // Injection paced at 16 kHz when asked
    assign inject = control[1];
    assign s_axis_samples_tready = control[0] && inject && count < 1024 && (!control[2] || pace == 0);
    assign push = control[0] && (inject ? s_axis_samples_tvalid && s_axis_samples_tready : mic_valid && count < 1024);
    assign selected = inject ? $signed({s_axis_samples_tdata, 2'b00}) : mic;
    assign pop = control[0] && count != 0 && fifo_q_valid && fft_ready;

    // For AXI-Lite, a write completes once both its address and data are valid
    // responses are always OKAY
    assign s_axi_awready = s_axi_awvalid && s_axi_wvalid && !s_axi_bvalid;
    assign s_axi_wready = s_axi_awready;
    assign s_axi_arready = !s_axi_rvalid;

    always_ff @(posedge aclk) begin
        if (push) begin
            fifo[write_pointer] <= selected;
        end
        fifo_q <= fifo[read_pointer];
    end

    always_ff @(posedge aclk) begin
        if (!aresetn) begin
            control <= 0;
            button <= 0;
            pressed <= 0;
            button_sync <= 0;
            debounce <= 0;
            write_pointer <= 0;
            read_pointer <= 0;
            count <= 0;
            fifo_q_valid <= 0;
            pace <= 0;
            s_axi_bvalid <= 0;
            s_axi_rvalid <= 0;
        end
        else begin
            // FIFO, a consumed word takes one cycle to refill fifo_q
            write_pointer <= write_pointer + push;
            read_pointer <= read_pointer + pop;
            count <= count + push - pop;
            fifo_q_valid <= count != 0 && !pop;
            if (pace != 0) begin
                pace <= pace - 1;
            end
            else if (push && inject) begin
                pace <= 6249;
            end

            // Button debounce: 10 ms
            button_sync <= {button_sync[0], pmod_btn_sw1};
            debounce <= button_sync[1] == pressed ? 0 : debounce + 1;
            if (&debounce && button_sync[1] != pressed) begin
                pressed <= button_sync[1];
                if (button_sync[1]) begin
                    button <= 1;
                end
            end

            if (s_axi_bvalid && s_axi_bready) begin
                s_axi_bvalid <= 0;
            end
            if (s_axi_awready) begin
                s_axi_bvalid <= 1;
                if (s_axi_awaddr == 0) begin
                    control <= s_axi_wdata;
                end
                if (s_axi_awaddr == 4 && s_axi_wdata[0]) begin
                    button <= 0;
                end
            end

            if (s_axi_rvalid && s_axi_rready) begin
                s_axi_rvalid <= 0;
            end
            if (s_axi_arvalid && s_axi_arready) begin
                s_axi_rvalid <= 1;
                s_axi_rdata <= s_axi_araddr == 0 ? control : s_axi_araddr == 4 ? 32'(button) : 0;
            end
        end
    end
endmodule
