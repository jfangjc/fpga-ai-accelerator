// I2S master for one microphone on the left channel
module stt_i2s (
    input logic clk, resetn, din,
    output logic bclk, lrclk,
    output logic signed [17:0] mic,
    output logic valid
);
    logic [26:0] phase;
    logic [27:0] sum;
    logic [5:0] slot;
    logic [1:0] sync;
    logic [23:0] shift;

    assign sum = {1'b0, phase} + 28'd2048000;

    always_ff @(posedge clk) begin
        sync <= {sync[0], din};
        valid <= 0;
        if (!resetn) begin
            phase <= 0;
            bclk <= 0;
            lrclk <= 0;
            slot <= 0;
            shift <= 0;
            mic <= 0;
            sync <= 0;
        end
        else if (sum >= 100000000) begin
            phase <= 27'(sum - 28'd100000000);
            bclk <= ~bclk;
            if (bclk) begin
                slot <= slot + 1;
                if (slot == 31) begin
                    lrclk <= 1;
                end
                else if (slot == 63) begin
                    lrclk <= 0;
                end
            end
            else begin
                if (slot >= 1 && slot <= 24) begin
                    shift <= {shift[22:0], sync[1]};
                end

                if (slot == 25) begin
                    mic <= shift[23:6];
                    valid <= 1;
                end
            end
        end
        else begin
            phase <= sum[26:0];
        end
    end
endmodule
