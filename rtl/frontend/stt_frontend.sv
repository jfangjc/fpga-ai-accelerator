module stt_frontend(
    input logic aclk,aresetn,
    input logic [11:0] s_axi_awaddr,input logic [2:0] s_axi_awprot,input logic s_axi_awvalid,output logic s_axi_awready,
    input logic [31:0] s_axi_wdata,input logic [3:0] s_axi_wstrb,input logic s_axi_wvalid,output logic s_axi_wready,
    output logic [1:0] s_axi_bresp,output logic s_axi_bvalid,input logic s_axi_bready,
    input logic [11:0] s_axi_araddr,input logic [2:0] s_axi_arprot,input logic s_axi_arvalid,output logic s_axi_arready,
    output logic [31:0] s_axi_rdata,output logic [1:0] s_axi_rresp,output logic s_axi_rvalid,input logic s_axi_rready,
    input logic [31:0] s_axis_samples_tdata,input logic [3:0] s_axis_samples_tkeep,input logic s_axis_samples_tlast,input logic s_axis_samples_tvalid,output logic s_axis_samples_tready,
    output logic [31:0] m_axis_features_tdata,output logic [3:0] m_axis_features_tkeep,output logic m_axis_features_tlast,output logic m_axis_features_tvalid,input logic m_axis_features_tready,
    output logic irq,pmod_i2s_bclk,pmod_i2s_lrclk,input logic pmod_i2s_dout,
    output logic pmod_i2s2_bclk,pmod_i2s2_lrclk,input logic pmod_i2s2_dout,output logic pmod_led_d1,input logic pmod_btn_sw1);
     // AXI protection attributes, alignment bits and sample packet boundaries are
     // intentionally ignored by this word-addressed, reset-delimited endpoint.
    logic unused_inputs;
    assign unused_inputs = ^{s_axi_awprot,s_axi_arprot,s_axi_araddr[1:0],
    s_axis_samples_tdata[31:18],s_axis_samples_tkeep,s_axis_samples_tlast,wa[1:0]};
    logic [31:0] ctrl,irq_period,irq_count,frames,overruns,underruns;
    logic overflow,underflow,button;
    logic [1:0] bs;
    logic [19:0] debounce;
    logic button_old;
    logic [11:0] wa;
    logic [31:0] wd;
    logic [3:0] ws;
    logic have_aw,have_w;
    logic signed [17:0] m1,m2,selected;
    logic mv;
    (* ram_style = "block" *) logic signed [17:0] fifo[0:1023];
    logic signed [17:0] fifo_q;
    logic fifo_valid;
    logic [9:0] wp,rp;
    logic [10:0] count;
    logic push,pop,fftready,done;
    logic [12:0] pace;
    logic [17:0] peak1,peak2;
    logic signed [47:0] dc1,dc2;
    logic [55:0] square1,square2;
    logic [15:0] statcount;
    logic [7:0] d1,d2;
    logic signed [15:0] gain;
    logic signed [17:0] beam_sample;
    stt_beamformer beam(aclk,aresetn,mv,m1,m2,d1,d2,gain,beam_sample);
    assign pmod_i2s2_bclk=pmod_i2s_bclk;
    assign pmod_i2s2_lrclk=pmod_i2s_lrclk;
    stt_i2s rx(aclk,aresetn,pmod_i2s_dout,pmod_i2s2_dout,ctrl[5],pmod_i2s_bclk,pmod_i2s_lrclk,m1,m2,mv);
    assign pmod_led_d1=ctrl[6];
    assign m_axis_features_tkeep=4'hf;
    assign s_axi_awready=!have_aw&&!s_axi_bvalid;
    assign s_axi_wready=!have_w&&!s_axi_bvalid;
    assign s_axi_arready=!s_axi_rvalid;
    assign s_axi_bresp=0;
    assign s_axi_rresp=0;
    assign s_axis_samples_tready=ctrl[0]&&ctrl[2:1]==3&&count<1024&&(!ctrl[3]||pace==0);
    assign push=ctrl[0]&&((ctrl[2:1]==3)?s_axis_samples_tvalid&&s_axis_samples_tready:mv&&count<1024);
    assign selected=ctrl[2:1]==3?$signed(s_axis_samples_tdata[17:0]):ctrl[2:1]==0?m1:ctrl[2:1]==1?m2: beam_sample;
    assign pop=count!=0&&fifo_valid&&fftready&&ctrl[0];
    stt_fft_mel dsp(aclk,aresetn,fifo_q,pop,fftready,m_axis_features_tdata,m_axis_features_tvalid,m_axis_features_tready,m_axis_features_tlast,done);
     // Synchronous memory reads; a consumed FIFO word inserts one refill cycle.
    always_ff @(posedge aclk)
    begin
        fifo_q <= fifo[rp];
        if (!aresetn) fifo_valid <= 0;
        else if (pop) fifo_valid <= 0;
        else if (count != 0) fifo_valid <= 1;
    end
    always_ff @(posedge aclk)
    begin
        if(!aresetn)
        begin
            ctrl<=0;
            irq_period<=1;
            irq_count<=0;
            irq<=0;
            frames<=0;
            overruns<=0;
            underruns<=0;
            overflow<=0;
            underflow<=0;
            wp<=0;
            rp<=0;
            count<=0;
            pace<=0;
            have_aw<=0;
            have_w<=0;
            s_axi_bvalid<=0;
            s_axi_rvalid<=0;
            s_axi_rdata<=0;
            bs<=0;
            debounce<=0;
            button<=0;
            button_old<=0;
            d1<=0;
            d2<=0;
            gain<=16384;
            peak1<=0;
            peak2<=0;
            dc1<=0;
            dc2<=0;
            square1<=0;
            square2<=0;
            statcount<=0;
        end
        else
        begin
            bs<={bs[0],pmod_btn_sw1};
            if(bs[1]==button_old)debounce<=0;
            else if(&debounce)
            begin
                button_old<=bs[1];
                debounce<=0;
                if(bs[1])
                begin
                    button<=1;
                    if(ctrl[7])irq<=1;
                end
            end
            else debounce<=debounce+1;
            if(pace!=0)pace<=pace-1;
            else if(push&&ctrl[2:1]==3)pace<=6249;
            if(push)
            begin
                fifo[wp]<=selected;
                wp<=wp+1;
            end
            if(pop)rp<=rp+1;
            case({push,pop})2'b10:count<=count+1;
                2'b01:count<=count-1;
                default:;
            endcase
            if(mv)
            begin
                if(statcount==0)
                begin
                    peak1<=0;
                    peak2<=0;
                    dc1<=0;
                    dc2<=0;
                    square1<=0;
                    square2<=0;
                end
                else
                begin
                    if(($signed(m1)<0?-$signed(m1):m1)>peak1)peak1<=($signed(m1)<0?-$signed(m1):m1);
                    if(($signed(m2)<0?-$signed(m2):m2)>peak2)peak2<=($signed(m2)<0?-$signed(m2):m2);
                    dc1<=dc1+48'($signed(m1));
                    dc2<=dc2+48'($signed(m2));
                    square1<=square1+$signed(m1)*$signed(m1);
                    square2<=square2+$signed(m2)*$signed(m2);
                end
                statcount<=statcount+1;
                if(ctrl[0]&&ctrl[2:1]!=3&&count==1024)
                begin
                    overflow<=1;
                    overruns<=overruns+1;
                end
            end
            if(done)
            begin
                frames<=frames+1;
                if(irq_count+1>=irq_period)
                begin
                    irq<=1;
                    irq_count<=0;
                end
                else irq_count<=irq_count+1;
            end
            if(s_axi_awvalid&&s_axi_awready)
            begin
                wa<=s_axi_awaddr;
                have_aw<=1;
            end
            if(s_axi_wvalid&&s_axi_wready)
            begin
                wd<=s_axi_wdata;
                ws<=s_axi_wstrb;
                have_w<=1;
            end
            if(s_axi_bvalid&&s_axi_bready)s_axi_bvalid<=0;
            if(have_aw&&have_w)
            begin
                have_aw<=0;
                have_w<=0;
                s_axi_bvalid<=1;
                case(wa[11:2])
                    0:for(integer b=0;
                    b<4;
                    b=b+1)if(ws[b])ctrl[b*8+:8]<=wd[b*8+:8];
                    2:if(ws[0])
                    begin
                        if(wd[0])irq<=0;
                        if(wd[1])overflow<=0;
                        if(wd[2])underflow<=0;
                        if(wd[3])button<=0;
                    end
                    3:for(integer b=0;
                    b<4;
                    b=b+1)if(ws[b])irq_period[b*8+:8]<=wd[b*8+:8];
                    8:
                    begin
                        if(ws[0])d1<=wd[7:0];
                        if(ws[1])d2<=wd[15:8];
                        if(ws[2])gain[7:0]<=wd[23:16];
                        if(ws[3])gain[15:8]<=wd[31:24];
                    end
                    default:;
                endcase
            end
            if(s_axi_rvalid&&s_axi_rready)s_axi_rvalid<=0;
            if(s_axi_arvalid&&s_axi_arready)
            begin
                s_axi_rvalid<=1;
                case(s_axi_araddr[11:2])
                    0:s_axi_rdata<=ctrl;
                    1:s_axi_rdata<={17'd0,count,button,underflow,overflow,irq};
                    2:s_axi_rdata<=0;
                    3:s_axi_rdata<=irq_period;
                    4:s_axi_rdata<=frames;
                    5:s_axi_rdata<=overruns;
                    6:s_axi_rdata<=underruns;
                    8:s_axi_rdata<={gain,d2,d1};
                    16:s_axi_rdata<={14'd0,peak1};
                    17:s_axi_rdata<=dc1[47:16];
                    18:s_axi_rdata<=square1[47:16];
                    19:s_axi_rdata<={24'b0,square1[55:48]};
                    20:s_axi_rdata<={14'd0,peak2};
                    21:s_axi_rdata<=dc2[47:16];
                    22:s_axi_rdata<=square2[47:16];
                    23:s_axi_rdata<={24'b0,square2[55:48]};
                    default:s_axi_rdata<=0;
                endcase
            end
        end
    end
endmodule
