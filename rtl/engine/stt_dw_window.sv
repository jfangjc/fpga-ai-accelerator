/*
    one channel's line buffer for the depthwise convolution
    -> the channel's whole input (up to 64 words of 16 frames) gets copied into a small block ram once
    -> a 48 byte window over it gives the 32 frames one tap needs (16 outputs at stride 2)
    -> every tap the window slides by the dilation and pulls in the next word once it runs past a word boundary
*/
module stt_dw_window (
    input logic clk, we,
    input logic [5:0] waddr,               // which 16 frame word of this channel is being loaded
    input logic [127:0] wdata,
    input logic prime, align, issue,       // the dw_ctrl state -> fill then line up then one tap per cycle
    input logic [2:0] prime_count,
    input logic [9:0] position,            // input frame this tap starts at
    input logic [1:0] dilation,
    output logic [255:0] samples           // 32 input frames for this tap
);
    (* ram_style = "block" *) logic [127:0] cache[64];
    logic [127:0] word;
    logic [383:0] window, slid;
    logic [5:0] address;
    logic crossing;

    // priming reads words 0 1 2 of the tile and after that it reads 3 words ahead so the next one is ready in time
    assign address = position[9:4] + (prime ? 6'(prime_count) : 6'd3);
    assign crossing = position[3:0] + dilation >= 16;
    assign slid = dilation == 2 ? window >> 16 : window >> 8;

    // loads the cache then keeps the window full while it slides
    always_ff @(posedge clk) begin
        if (we) begin
            cache[waddr] <= wdata;
        end
        if (issue || prime && prime_count < 4) begin
            word <= cache[address];
        end
        // -> the read takes a cycle so the words land one step after they're asked for
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
            // -> still inside the word so just slide
            if (!crossing) begin
                window <= slid;
            end
            // -> dilation 2 from an odd byte so the next word goes in at byte 31
            else if (dilation == 2 && position[0]) begin
                window <= {8'd0, word, slid[247:0]};
            end
            // -> crossed a word boundary so the next word goes in on top
            else begin
                window <= {word, slid[255:0]};
            end
        end
    end
endmodule
