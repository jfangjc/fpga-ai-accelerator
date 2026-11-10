// Unsigned 64 x 32 multiplier, one registered 16 x 16 DSP reused eight times.
// Operands remain internal until done. Result is exact (no rounding).
module stt_serial_mul (
    input logic clk, resetn, start,
    input logic [63:0] a,
    input logic [31:0] b,
    output logic [95:0] result,
    output logic done
);
    logic [63:0] aa;
    logic [31:0] bb;
    logic [2:0] part;
    logic busy, pending;
    logic [31:0] product;
    logic [95:0] accumulator;
    logic [6:0] shift;
    always_ff @(posedge clk) begin
        done <= 1'b0;
        if (!resetn) begin
            busy <= 1'b0;
            pending <= 1'b0;
        end else if (start && !busy) begin
            aa <= a;
            bb <= b;
            part <= 0;
            accumulator <= 0;
            busy <= 1'b1;
            pending <= 1'b0;
        end else if (busy) begin
            if (!pending) begin
                product <= aa[part[2:1]*16 +: 16] * bb[part[0]*16 +: 16];
                shift <= {3'b0,part[2:1]} * 7'd16 + {6'b0,part[0]} * 7'd16;
                pending <= 1'b1;
            end else begin
                accumulator <= accumulator + ({64'b0,product} << shift);
                pending <= 1'b0;
                if (part == 7) begin
                    result <= accumulator + ({64'b0,product} << shift);
                    done <= 1'b1;
                    busy <= 1'b0;
                end else part <= part + 1'b1;
            end
        end
    end
endmodule
