module stt_i2s(input logic clk,resetn,din1,din2,channel,output logic bclk,lrclk,output logic signed [17:0] mic1,mic2,output logic valid);
    logic [26:0] phase;
    logic [27:0] sum;
    logic [5:0] slot;
    logic [1:0] sync1,sync2;
    logic [23:0] sh1,sh2;
    assign sum={1'b0,phase}+28'd2048000;
    always_ff @(posedge clk)
    begin
        sync1<={sync1[0],din1};
        sync2<={sync2[0],din2};
        valid<=0;
        if(!resetn)
        begin
            phase<=0;
            bclk<=0;
            lrclk<=0;
            slot<=0;
            sh1<=0;
            sh2<=0;
            mic1<=0;
            mic2<=0;
            sync1<=0;
            sync2<=0;
        end
        else if(sum>=100000000)
        begin
            phase<=27'(sum-28'd100000000);
            bclk<=~bclk;
            if(bclk)
            begin
                slot<=slot+1;
                if(slot==31)lrclk<=1;
                else if(slot==63)lrclk<=0;
            end
            else
            begin
                if(slot[4:0]>=1 && slot[4:0]<=24)
                begin
                    sh1<={sh1[22:0],sync1[1]};
                    sh2<={sh2[22:0],sync2[1]};
                end
                if(slot[4:0]==25 && slot[5]==channel)
                begin
                    mic1<=sh1[23:6];
                    mic2<=sh2[23:6];
                    valid<=1;
                end
            end
        end
        else phase<=sum[26:0];
    end
endmodule
