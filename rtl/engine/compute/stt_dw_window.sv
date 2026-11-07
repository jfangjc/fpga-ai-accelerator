// One channel's line buffer for the depthwise convolution
module stt_dw_window (
    input logic clk, we,
    input logic [5:0] waddr,
    input logic [127:0] wdata,
    input logic prime, align, issue,
    input logic [2:0] prime_count,
    input logic [9:0] position,
    input logic [1:0] dilation,
    output logic [255:0] samples
);
    (* ram_style = "block" *) logic [127:0] cache[64];
    logic [127:0] word;
    logic [383:0] window, slid;
    logic [5:0] address;
    logic crossing;

    assign address = position[9:4] + (prime ? 6'(prime_count) : 6'd3);
    assign crossing = position[3:0] + dilation >= 16;
    assign slid = dilation == 2 ? window >> 16 : window >> 8;

    always_ff @(posedge clk) begin
        if (we) begin
            cache[waddr] <= wdata;
        end
        if (issue || prime && prime_count < 4) begin
            word <= cache[address];
        end
        if (prime) begin
            case (prime_count)
                1: window[127:0] <= word;
                2: window[255:128] <= word;
                3: window[383:256] <= word;
                default: ;
            endcase
        end
        if (align) begin
            window <= window >> 8;
        end
        if (issue) begin
            samples <= window[255:0];
            if (!crossing) begin
                window <= slid;
            end
            else if (dilation == 2 && position[0]) begin
                window <= {8'd0, word, slid[247:0]};
            end
            else begin
                window <= {word, slid[255:0]};
            end
        end
    end
endmodule
