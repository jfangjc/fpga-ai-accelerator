`timescale 1ns/1ps
// Checked INT32 bias plus positive Q31 affine UINT8 requantization.
// Two elastic stages; every lane preserves signed nearest-even ties.
module fused_affine_quant #(parameter LANES=16)(
    input wire aclk,aresetn,
    input wire [LANES*32-1:0] s_acc,s_bias,s_multiplier,
    input wire [LANES*6-1:0] s_shift,
    input wire [LANES*8-1:0] s_zero,
    input wire s_valid,output wire s_ready,
    output reg [LANES*8-1:0] m_data,
    output wire m_valid,input wire m_ready,output reg m_error
);
    reg pv,ov;
    reg signed [63:0] products[0:LANES-1];
    reg [5:0] shifts[0:LANES-1];
    reg [7:0] zeros[0:LANES-1];
    reg [LANES-1:0] bad;
    wire out_ready=!ov || m_ready;
    wire prod_ready=!pv || out_ready;
    assign s_ready=aresetn && prod_ready;
    assign m_valid=aresetn && ov;
    function [7:0] rounded_byte;
        input signed [63:0] product;
        input [5:0] shift;
        input [7:0] zero;
        input invalid;
        reg signed [63:0] quotient,result;
        reg [63:0] remainder,half;
        begin
            if(invalid) rounded_byte=0;
            else begin
                quotient=product >>> shift;
                remainder=product & ((64'd1 << shift)-1);
                half=64'd1 << (shift-1);
                result=quotient+((remainder>half)||((remainder==half)&&quotient[0]))+$signed({1'b0,zero});
                if(result<0)rounded_byte=0;
                else if(result>255)rounded_byte=255;
                else rounded_byte=result[7:0];
            end
        end
    endfunction
    reg signed [32:0] added;
    integer i;
    always @(posedge aclk) begin
        if(!aresetn) begin pv<=0;ov<=0;m_data<=0;m_error<=0;end
        else begin
            if(out_ready) begin
                ov<=pv;
                if(pv) begin
                    for(i=0;i<LANES;i=i+1)
                        m_data[i*8+:8]<=rounded_byte(products[i],shifts[i],zeros[i],bad[i]);
                    m_error<=|bad;
                end
            end
            if(prod_ready) begin
                pv<=s_valid;
                if(s_valid) for(i=0;i<LANES;i=i+1) begin
                    added=$signed(s_acc[i*32+:32])+$signed({s_bias[i*32+31],s_bias[i*32+:32]});
                    products[i]<=$signed(added[31:0])*$signed(s_multiplier[i*32+:32]);
                    shifts[i]<=s_shift[i*6+:6];zeros[i]<=s_zero[i*8+:8];
                    bad[i]<=added[32]!=added[31] || s_multiplier[i*32+31] ||
                            s_shift[i*6+:6]==0 || s_shift[i*6+:6]==63;
                end
            end
        end
    end
endmodule
