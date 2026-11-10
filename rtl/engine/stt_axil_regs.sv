// AXI4-Lite register file and interrupt logic of stt_engine. Address and
// write-data channels are accepted independently (in either order); a write
// completes when both have arrived. Responses are always OKAY; unmapped
// addresses read as zero and ignore writes. See docs/engine.md for the map.
module stt_axil_regs
    import stt_engine_pkg::*;
(
    input  logic        clk,
    input  logic        rst,           // aresetn only: soft reset keeps settings
    // Registers are word aligned; byte-address bits [1:0] are ignored.
    /* verilator lint_off UNUSEDSIGNAL */
    input  logic [7:0]  s_awaddr,
    /* verilator lint_on UNUSEDSIGNAL */
    input  logic        s_awvalid,
    output logic        s_awready,
    input  logic [31:0] s_wdata,
    input  logic [3:0]  s_wstrb,
    input  logic        s_wvalid,
    output logic        s_wready,
    output logic [1:0]  s_bresp,
    output logic        s_bvalid,
    input  logic        s_bready,
    /* verilator lint_off UNUSEDSIGNAL */
    input  logic [7:0]  s_araddr,
    /* verilator lint_on UNUSEDSIGNAL */
    input  logic        s_arvalid,
    output logic        s_arready,
    output logic [31:0] s_rdata,
    output logic [1:0]  s_rresp,
    output logic        s_rvalid,
    input  logic        s_rready,
    // Engine interface
    output logic        soft_reset,    // one-cycle pulse
    output logic [31:0] watchdog,
    output logic        irq,
    input  logic        busy,
    input  logic        responding,
    input  logic        resp_done,     // final response beat accepted
    input  logic [3:0]  resp_status,
    input  logic [31:0] resp_tag,
    input  logic [63:0] resp_cycles,
    input  logic [31:0] resp_active,
    input  logic [31:0] resp_stall
);
    localparam logic [5:0] R_CONTROL = 6'h00, R_STATUS = 6'h01, R_VERSION = 6'h02,
        R_IRQ_EN = 6'h03, R_IRQ_ST = 6'h04, R_TAG = 6'h05, R_LSTATUS = 6'h06,
        R_WATCHDOG = 6'h07, R_CYC_LO = 6'h08, R_CYC_HI = 6'h09, R_RESP = 6'h0a,
        R_ERRORS = 6'h0b, R_ACTIVE = 6'h0c, R_STALL = 6'h0d, R_SCRATCH = 6'h0e;

    logic        aw_hold, w_hold, do_write;
    logic [5:0]  aw_index;
    logic [31:0] w_data;
    logic [3:0]  w_strb;
    logic [1:0]  irq_en, irq_st;
    logic [31:0] last_tag, last_status, last_active, last_stall, scratch;
    logic [31:0] responses, errors;
    logic [63:0] last_cycles;

    function automatic logic [31:0] merge(input logic [31:0] old, input logic [31:0] d,
                                          input logic [3:0] strb);
        for (int b = 0; b < 4; b++) if (strb[b]) old[8*b +: 8] = d[8*b +: 8];
        return old;
    endfunction

    assign s_awready = !aw_hold && !s_bvalid;
    assign s_wready = !w_hold && !s_bvalid;
    assign do_write = aw_hold && w_hold && !s_bvalid;
    assign s_bresp = 2'b00;
    assign s_arready = !s_rvalid;
    assign s_rresp = 2'b00;

    always_ff @(posedge clk) begin
        soft_reset <= 1'b0;
        if (rst) begin
            aw_hold <= 1'b0;
            w_hold <= 1'b0;
            s_bvalid <= 1'b0;
            s_rvalid <= 1'b0;
            irq_en <= '0;
            irq_st <= '0;
            irq <= 1'b0;
            watchdog <= 32'h0100_0000;
            scratch <= '0;
            last_tag <= '0;
            last_status <= '0;
            last_cycles <= '0;
            last_active <= '0;
            last_stall <= '0;
            responses <= '0;
            errors <= '0;
        end else begin
            if (s_awvalid && s_awready) begin
                aw_hold <= 1'b1;
                aw_index <= s_awaddr[7:2];
            end
            if (s_wvalid && s_wready) begin
                w_hold <= 1'b1;
                w_data <= s_wdata;
                w_strb <= s_wstrb;
            end
            if (s_bvalid && s_bready) s_bvalid <= 1'b0;
            if (do_write) begin
                aw_hold <= 1'b0;
                w_hold <= 1'b0;
                s_bvalid <= 1'b1;
                case (aw_index)
                    R_CONTROL: if (w_strb[0] && w_data[0]) soft_reset <= 1'b1;
                    R_IRQ_EN: if (w_strb[0]) irq_en <= w_data[1:0];
                    R_IRQ_ST: if (w_strb[0]) irq_st <= irq_st & ~w_data[1:0];
                    R_WATCHDOG: watchdog <= merge(watchdog, w_data, w_strb);
                    R_SCRATCH: scratch <= merge(scratch, w_data, w_strb);
                    default: ;
                endcase
            end
            // Soft reset discards pending interrupts; completion wins over W1C.
            if (soft_reset) irq_st <= '0;
            if (resp_done) begin
                irq_st <= irq_st | {resp_status != ST_OK, 1'b1};
                last_tag <= resp_tag;
                last_status <= {28'd0, resp_status};
                last_cycles <= resp_cycles;
                last_active <= resp_active;
                last_stall <= resp_stall;
                responses <= responses + 32'd1;
                if (resp_status != ST_OK) errors <= errors + 32'd1;
            end
            irq <= |(irq_st & irq_en);
            if (s_arvalid && s_arready) begin
                s_rvalid <= 1'b1;
                case (s_araddr[7:2])
                    R_STATUS:   s_rdata <= {29'd0, irq, responding, busy};
                    R_VERSION:  s_rdata <= ENGINE_VERSION;
                    R_IRQ_EN:   s_rdata <= {30'd0, irq_en};
                    R_IRQ_ST:   s_rdata <= {30'd0, irq_st};
                    R_TAG:      s_rdata <= last_tag;
                    R_LSTATUS:  s_rdata <= last_status;
                    R_WATCHDOG: s_rdata <= watchdog;
                    R_CYC_LO:   s_rdata <= last_cycles[31:0];
                    R_CYC_HI:   s_rdata <= last_cycles[63:32];
                    R_RESP:     s_rdata <= responses;
                    R_ERRORS:   s_rdata <= errors;
                    R_ACTIVE:   s_rdata <= last_active;
                    R_STALL:    s_rdata <= last_stall;
                    R_SCRATCH:  s_rdata <= scratch;
                    default:    s_rdata <= 32'd0;
                endcase
            end else if (s_rvalid && s_rready) begin
                s_rvalid <= 1'b0;
            end
        end
    end
endmodule
