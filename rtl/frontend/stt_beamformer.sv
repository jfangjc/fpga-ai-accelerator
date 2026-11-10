// Integer delay-and-sum beamformer. History stores the preceding DC-filter
// output, matching the documented one-sample pipeline. Warm up 256 samples.
module stt_beamformer (
    input logic clk, resetn, valid,
    input logic signed [17:0] mic1, mic2,
    input logic [7:0] delay1, delay2,
    input logic signed [15:0] gain,
    output logic signed [17:0] sample
);
    (* ram_style = "block" *) logic signed [17:0] history1 [0:255];
    (* ram_style = "block" *) logic signed [17:0] history2 [0:255];
    logic [7:0] pointer;
    logic signed [31:0] hp1, hp2;
    logic signed [17:0] previous1, previous2, delayed1, delayed2;
    logic signed [63:0] feedback1, feedback2;
    logic signed [18:0] sum;
    logic signed [34:0] weighted;
    assign feedback1 = 64'(hp1) * 64'sd32604;
    assign feedback2 = 64'(hp2) * 64'sd32604;
    assign sum = 19'(delayed1) + 19'(delayed2);
    assign weighted = sum * gain;
    assign sample = 18'(weighted >>> 15);
    always_ff @(posedge clk) begin
        delayed1 <= history1[8'(pointer-delay1)];
        delayed2 <= history2[8'(pointer-delay2)];
        if (valid) begin
            history1[pointer] <= hp1[17:0];
            history2[pointer] <= hp2[17:0];
        end
        if (!resetn) begin
            pointer <= 0;
            hp1 <= 0; hp2 <= 0;
            previous1 <= 0; previous2 <= 0;
        end else if (valid) begin
            pointer <= pointer + 1'b1;
            hp1 <= 32'(64'(mic1)-64'(previous1)+(feedback1 >>> 15));
            hp2 <= 32'(64'(mic2)-64'(previous2)+(feedback2 >>> 15));
            previous1 <= mic1; previous2 <= mic2;
        end
    end
endmodule
