`timescale 1ns/1ps
// Inference experiment: legacy MAC addresses return SLVERR. Audio is unqualified at the new clock.
// 8000 STATUS: [10:0] count, [16] empty, [17] full, [18] sticky overflow.
// 8004 DATA: pop a signed 24-bit sample, sign extended to 32 bits.
// Reading empty DATA returns SLVERR. 8008 dropped, 800c captured frames.
// IRQ is level sensitive: count >=160 (10 ms) or overflow; reset clears it.
// Only aligned, full-word writes are accepted. Audio registers are read-only.
module voice_axi (
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME s_axi_aclk, ASSOCIATED_BUSIF S_AXI, ASSOCIATED_RESET s_axi_aresetn" *)
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 s_axi_aclk CLK" *) input wire s_axi_aclk,
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME s_axi_aresetn, POLARITY ACTIVE_LOW" *)
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 s_axi_aresetn RST" *) input wire s_axi_aresetn,
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXI, PROTOCOL AXI4LITE, DATA_WIDTH 32, ADDR_WIDTH 16, READ_WRITE_MODE READ_WRITE, HAS_BURST 0, HAS_LOCK 0, HAS_CACHE 0, HAS_QOS 0, HAS_REGION 0, SUPPORTS_NARROW_BURST 0, NUM_READ_OUTSTANDING 1, NUM_WRITE_OUTSTANDING 1" *)
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR" *) input wire [15:0] s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWPROT" *) input wire [2:0] s_axi_awprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *) input wire s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *) output wire s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *) input wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *) input wire [3:0] s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *) input wire s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *) output wire s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *) output reg [1:0] s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *) output reg s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *) input wire s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *) input wire [15:0] s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARPROT" *) input wire [2:0] s_axi_arprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *) input wire s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *) output wire s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *) output reg [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *) output reg [1:0] s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *) output reg s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *) input wire s_axi_rready,
    output wire mic_bclk, output wire mic_ws, input wire mic_data,
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME irq, SENSITIVITY LEVEL_HIGH" *)
    (* X_INTERFACE_INFO = "xilinx.com:signal:interrupt:1.0 irq INTERRUPT" *) output wire irq
);
    wire rst = ~s_axi_aresetn;
    reg aw_pending, w_pending;
    reg [15:0] awaddr;
    reg [31:0] wdata;
    reg [3:0] wstrb;
    assign s_axi_awready = !aw_pending && !s_axi_bvalid;
    assign s_axi_wready = !w_pending && !s_axi_bvalid;
    assign s_axi_arready = !s_axi_rvalid;
    wire write_commit = aw_pending && w_pending && !s_axi_bvalid;
    wire write_ok = 1'b0; // No writable capture registers; legacy MAC disabled.
    wire [31:0] mac_read;
    // Legacy MAC is absent in this separate inference image.
    assign mac_read = 32'b0;
    wire [23:0] sample;
    wire sample_valid;
    sph0645_rx receiver(.clk(s_axi_aclk),.rst(rst),.mic_data(mic_data),
        .mic_bclk(mic_bclk),.mic_ws(mic_ws),.sample_data(sample),.sample_valid(sample_valid));
    // Distributed memory provides an asynchronous head read for AXI reads.
    (* ram_style = "distributed" *) reg [23:0] fifo [0:1023];
    reg [9:0] read_ptr, write_ptr;
    reg [10:0] count;
    reg [31:0] dropped, captured;
    reg overflow;
    wire read_accept = s_axi_arvalid && s_axi_arready;
    wire pop = read_accept && s_axi_araddr == 16'h8004 && count != 0;
    wire push = sample_valid && (count < 1024 || pop);
    assign irq = count >= 160 || overflow;
    always @(posedge s_axi_aclk) begin
        if (rst) begin
            aw_pending <= 0; w_pending <= 0; awaddr <= 0; wdata <= 0; wstrb <= 0;
            s_axi_bvalid <= 0; s_axi_bresp <= 0;
            s_axi_rvalid <= 0; s_axi_rdata <= 0; s_axi_rresp <= 0;
            read_ptr <= 0; write_ptr <= 0; count <= 0;
            dropped <= 0; captured <= 0; overflow <= 0;
        end else begin
            if (s_axi_awvalid && s_axi_awready) begin awaddr <= s_axi_awaddr; aw_pending <= 1; end
            if (s_axi_wvalid && s_axi_wready) begin wdata <= s_axi_wdata; wstrb <= s_axi_wstrb; w_pending <= 1; end
            if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 0;
            if (write_commit) begin
                aw_pending <= 0; w_pending <= 0;
                s_axi_bvalid <= 1; s_axi_bresp <= write_ok ? 2'b00 : 2'b10;
            end
            if (s_axi_rvalid && s_axi_rready) s_axi_rvalid <= 0;
            if (read_accept) begin
                s_axi_rvalid <= 1; s_axi_rresp <= 0; s_axi_rdata <= 0;
                if (s_axi_araddr[1:0] != 0) s_axi_rresp <= 2'b10;
                else if (!s_axi_araddr[15]) begin s_axi_rdata <= 0; s_axi_rresp <= 2'b10; end
                else case (s_axi_araddr)
                    16'h8000: s_axi_rdata <= {13'd0,overflow,(count == 1024),(count == 0),5'd0,count};
                    16'h8004: begin
                        if (count == 0) s_axi_rresp <= 2'b10;
                        else s_axi_rdata <= {{8{fifo[read_ptr][23]}},fifo[read_ptr]};
                    end
                    16'h8008: s_axi_rdata <= dropped;
                    16'h800c: s_axi_rdata <= captured;
                    default: s_axi_rresp <= 2'b10;
                endcase
            end
            if (sample_valid) captured <= captured + 1'b1;
            if (push) begin fifo[write_ptr] <= sample; write_ptr <= write_ptr + 1'b1; end
            if (pop) read_ptr <= read_ptr + 1'b1;
            case ({push,pop})
                2'b10: count <= count + 1'b1;
                2'b01: count <= count - 1'b1;
                default: count <= count;
            endcase
            if (sample_valid && !push) begin dropped <= dropped + 1'b1; overflow <= 1; end
        end
    end
endmodule


