`timescale 1ns/1ps
module tb_qn_branch512_ab_payload_reset;
 reg clk=0;always #5 clk=~clk;
 reg rstn=0,valid=0,last=0,ready=0,force_stall=0;
 reg [127:0] data=0;
 wire in_ready,out_valid,out_last,busy,done,error;
 wire [127:0] out_data;
 qn_cached_axis dut(clk,rstn,data,valid,in_ready,last,out_data,out_valid,ready,out_last,busy,done,error);
 integer expected[0:1048575];
 integer intermediate[0:65535];
 integer received=0,cycle=0,cases=0,payload=0,expected_mac=0;
 integer case_start=0,branch_payload=0;reg branch_case=0;
 integer chain_x[0:65535],chain_y[0:65535];
 reg checking=0,bulk=0,quantized=0,previous_stall=0;
 reg [127:0] held_data,expected_header;
 reg held_last;
 integer j,test_slot=0,test_kernel=11,slot_index;
 integer test_slots[0:5];
 always @(negedge clk)begin cycle=cycle+1;ready=!force_stall && cycle%11>2;end
 always @(posedge clk)begin
  if(!rstn)begin previous_stall=0;received=0;end
  else begin
   if(checking && cycle-case_start>1200000)$fatal(1,"Case watchdog cases=%0d state=%0d received=%0d expected=%0d",cases,dut.state,received,payload);
   if(previous_stall && (!out_valid || out_data!==held_data || out_last!==held_last))$fatal(1,"Stalled output changed");
   previous_stall=out_valid && !ready;held_data=out_data;held_last=out_last;
   if(checking && out_valid && ready)begin
    if(received==0 || (branch_case && received==branch_payload+1))begin if(out_data!==expected_header)$fatal(1,"ACK actual %h expected %h",out_data,expected_header);end
    else if(bulk && received==payload+1)begin
     if(out_data[31:0]!==expected_mac || out_data[127:96]<expected_mac)$fatal(1,"Hardware cycle counter mismatch %h expected issues %0d",out_data,expected_mac);
    end else if(quantized)begin
     for(j=0;j<16;j=j+1)if(out_data[j*8+:8]!==expected[(received-1-((branch_case && received>branch_payload+1)?1:0))*16+j][7:0])
      $fatal(1,"Quant output beat %0d byte %0d got %0d expected %0d",received,j,out_data[j*8+:8],expected[(received-1-((branch_case && received>branch_payload+1)?1:0))*16+j]);
    end else for(j=0;j<4;j=j+1)if($signed(out_data[j*32+:32])!==expected[(received-1)*4+j])$fatal(1,"Raw output mismatch beat %0d lane %0d",received,j);
    if(out_last!==(received==payload+(bulk?1:0)))$fatal(1,"TLAST mismatch beat %0d",received);
    received=received+1;
   end
  end
 end
 function automatic [127:0] header(input integer op,k,m,t,tag);
  header={tag[31:0],t[15:0],k[15:0],m[15:0],test_slot[7:0],op[7:0],32'h32434e51};
 endfunction
 function automatic integer xv(input integer k,t,seed);xv=(k*17+t*7+seed)%256;endfunction
 function automatic integer wv(input integer k,o,seed);wv=(k*31+o*13+seed*3)%256-128;endfunction
 function automatic integer requant(input reg signed[63:0] total,input integer zero);
  reg signed[63:0] p,q,r,den;
  begin p=total*64'sh40000000;den=64'sh2000000000;q=p>>>37;r=p-(q<<<37);
   if(r>(den>>>1) || (r==(den>>>1) && q[0]))q=q+1;
   q=q+zero;requant=q<0?0:q>255?255:q;
  end
 endfunction
 task send(input reg[127:0] value,input reg ending);
  begin if(clk!==0)@(negedge clk);data=value;valid=1;last=ending;do @(posedge clk);while(!in_ready);@(negedge clk);valid=0;last=0;end
 endtask
 task expect_ack(input integer stat,k,m,tag,n,input reg b,q);
  begin expected_header={tag[31:0],m[15:0],k[15:0],stat[31:0],32'h32524e51};
   branch_case=0;payload=n;bulk=b;quantized=q;received=0;checking=1;case_start=cycle;end
 endtask
 task finish(input reg bad);
  begin wait(done);@(negedge clk);if(received!=payload+1+(bulk?1:0) || busy || error!==bad)$fatal(1,"Completion mismatch received %0d payload %0d error %b",received,payload,error);
   checking=0;cases=cases+1;$display("Completed case %0d at cycle %0d",cases,cycle);end
 endtask
 task load(input integer k,m,seed,tag,zx,zy,input reg q);
  integer g,i,o,sum,correction;reg [127:0] beat;
  begin
   expect_ack(0,k,m,tag,0,0,0);send(header(12,k,m,0,tag),0);
   for(g=0;g<(m+15)/16;g=g+1)for(i=0;i<k;i=i+1)begin
    beat=0;for(o=0;o<16;o=o+1)if(g*16+o<m)beat[o*8+:8]=wv(i,g*16+o,seed);
    send(beat,!q && g==(m+15)/16-1 && i==k-1);
   end
   if(q)for(g=0;g<(m+15)/16;g=g+1)for(o=0;o<16;o=o+1)begin
    sum=0;for(i=0;i<k;i=i+1)if(g*16+o<m)sum=sum+wv(i,g*16+o,seed);
    correction=(128-zx)*sum+(g*16+o<m?(g*16+o)*57-231:0);
    beat=0;beat[31:0]=correction;beat[63:32]=g*16+o<m?32'h40000000:0;
    beat[69:64]=g*16+o<m?37:1;beat[79:72]=g*16+o<m?zy:0;
    send(beat,g==(m+15)/16-1 && o==15);
   end
   finish(0);
  end
 endtask
 task run_case(input integer k,m,t,seed,tag,zx,zy,input reg b,q);
  integer tile,g,time_index,o,i,idx,n,groups,nt,channel;
  reg signed[63:0] total;reg [127:0] beat;
  begin groups=(m+15)/16;nt=b?(t+15)/16:1;
   n=nt*groups*(q?16:64);
   for(tile=0;tile<nt;tile=tile+1)for(g=0;g<groups;g=g+1)for(time_index=0;time_index<16;time_index=time_index+1)for(o=0;o<16;o=o+1)begin
    channel=g*16+o;total=0;
    if(channel<m)begin
     for(i=0;i<k;i=i+1)total=total+wv(i,channel,seed)*(xv(i,tile*16+time_index,seed)-(q?zx:128));
     if(q)total=total+channel*57-231;
    end
    idx=((tile*groups+g)*16+time_index)*16+o;
    expected[idx]=channel<m?(q?requant(total,zy):total):0;
   end
   expected_mac=nt*((groups+1)/2)*k;
   expect_ack(0,k,m,tag,n,b,q);send(header(b?6:q?5:2,k,m,b?t:0,tag),0);
   if(b)for(i=0;i<k;i=i+1)for(tile=0;tile<nt;tile=tile+1)begin
    beat=0;for(time_index=0;time_index<16;time_index=time_index+1)beat[time_index*8+:8]=xv(i,tile*16+time_index,seed);
    send(beat,i==k-1 && tile==nt-1);
   end else for(i=0;i<k;i=i+1)begin
    beat=0;for(time_index=0;time_index<16;time_index=time_index+1)beat[time_index*8+:8]=xv(i,time_index,seed)-128;
    send(beat,i==k-1);
   end
   finish(0);
  end
 endtask
 task load_dw(input integer k,seed,tag,zx,zy);
  integer c,i,h;reg [127:0] beat;
  begin
   expect_ack(0,k,test_kernel,tag,0,0,0);send(header(13,k,test_kernel,0,tag),0);
   for(c=0;c<k;c=c+1)for(h=0;h<2;h=h+1)begin
    beat=0;for(i=0;i<16;i=i+1)if(h*16+i<test_kernel)beat[i*8+:8]=wv(h*16+i,c,seed);
    send(beat,0);
   end
   for(c=0;c<k;c=c+1)begin
    beat=0;beat[31:0]=c*3-57;beat[63:32]=32'h40000000;beat[69:64]=37;
    beat[79:72]=zy;beat[103:96]=zx;send(beat,c==k-1);
   end
   finish(0);
  end
 endtask
 task pair_case(input integer k,m,t,v,seed,tag,zx,dzy,pzy);
  integer nt,groups,tile,g,p,o,i,d,idx,channel;reg signed[63:0] total,inner;
  reg[127:0] beat;
  begin
   nt=(t+15)/16;groups=(m+15)/16;
   for(i=0;i<k;i=i+1)for(p=0;p<nt*16;p=p+1)begin
     inner=i*3-57;
     for(d=0;d<test_kernel;d=d+1)begin idx=p+d-test_kernel/2;
      if(idx>=0 && idx<t)inner=inner+wv(d,i,seed)*(xv(i,idx,seed)-zx);
     end
     intermediate[i*nt*16+p]=p>=v?dzy:requant(inner,dzy);
   end
   for(tile=0;tile<nt;tile=tile+1)for(g=0;g<groups;g=g+1)for(p=0;p<16;p=p+1)for(o=0;o<16;o=o+1)begin
    channel=g*16+o;total=channel*57-231;
    for(i=0;i<k;i=i+1)total=total+wv(i,channel,seed)*(intermediate[i*nt*16+tile*16+p]-dzy);
    expected[((tile*groups+g)*16+p)*16+o]=channel<m?requant(total,pzy):0;
   end
   expected_mac=nt*((groups+1)/2)*k;
   expect_ack(0,k,m,tag,nt*groups*16,1,1);send(header(14,k,m,t,tag),0);
   send(v,0);
   for(i=0;i<k;i=i+1)for(tile=0;tile<nt;tile=tile+1)begin
    beat=0;for(p=0;p<16;p=p+1)beat[p*8+:8]=xv(i,tile*16+p,seed);
    send(beat,i==k-1 && tile==nt-1);
   end
   finish(0);
  end
 endtask


 reg inject_bad=0;
 task stream_case(input integer t,v,n,kbase);
  integer stage,kernel,seed,zx,dzy,pzy,g,k,o,c,p,tile,part,nt,sum;reg [127:0] beat;
  begin
   nt=(t+15)/16;test_slot=0;
   if(!inject_bad)begin
    $readmemh($sformatf("C:/Users/jfang/Documents/Vivado/Prototype_v2/verification/adaptive-vectors/stream_%0d_%0d_%0d_%0d.mem",t,v,n,kbase),expected,0,nt*16*16*16-1);
    expected_mac=n*nt*8*256;expect_ack(0,256,256,901,nt*16*16,1,1);
   end else expect_ack(3,256,256,901,0,0,0);
   send(header(17,256,256,t,901),0);beat=0;beat[12:0]=v;beat[23:16]=n;send(beat,0);
   for(stage=0;stage<n;stage=stage+1)begin
    kernel=(kbase-1+stage*2)%42+1;beat=0;beat[4:0]=stage;beat[15:8]=17+stage*10;beat[21:16]=kernel;beat[63:32]=101+stage;send(beat,0);
   end
   for(stage=0;stage<n;stage=stage+1)begin
    kernel=(kbase-1+stage*2)%42+1;seed=11+stage*5;zx=128+stage*17;dzy=127-stage*17;pzy=128+stage*13;
    for(g=0;g<16;g=g+1)for(k=0;k<256;k=k+1)begin
     beat=0;for(o=0;o<16;o=o+1)beat[o*8+:8]=wv(k,g*16+o,seed);send(beat,0);
    end
    for(g=0;g<16;g=g+1)for(o=0;o<16;o=o+1)begin
     sum=0;for(k=0;k<256;k=k+1)sum=sum+wv(k,g*16+o,seed);
     beat=0;beat[31:0]=(128-dzy)*sum+(g*16+o)*57-231;
     beat[63:32]=32'h40000000;beat[69:64]=37;beat[79:72]=pzy;
     if(inject_bad && stage==0 && g==0 && o==0)beat[32]=1;
     send(beat,0);
    end
    for(c=0;c<256;c=c+1)for(part=0;part<3;part=part+1)begin
     beat=0;for(p=0;p<16;p=p+1)if(part*16+p<kernel)beat[p*8+:8]=wv(part*16+p,c,seed);send(beat,0);
    end
    for(c=0;c<256;c=c+1)begin
     beat=0;beat[31:0]=c*3-57;beat[63:32]=32'h40000000;beat[69:64]=37;beat[79:72]=dzy;beat[103:96]=zx;send(beat,0);
    end
   end
   for(stage=0;stage<n-1;stage=stage+1)for(g=0;g<16;g=g+1)begin
    beat=0;for(p=0;p<16;p=p+1)beat[p*8+:8]=((g*16+p)*3+stage*7)%256;send(beat,0);
   end
   for(c=0;c<256;c=c+1)for(tile=0;tile<nt;tile=tile+1)begin
    beat=0;for(p=0;p<16;p=p+1)beat[p*8+:8]=xv(c,tile*16+p,11);
    send(beat,c==255 && tile==nt-1);
   end
   finish(inject_bad);
  end
 endtask

 task residual_case(input integer t,v,n,kbase);
  integer stage,i,kernel,seed,zx,dzy,pzy,g,k,o,c,p,tile,part,nt,sum,bias_value;reg [127:0] beat;
  begin
   nt=(t+15)/16;test_slot=0;
   if(!inject_bad)begin
    $readmemh($sformatf("C:/Users/jfang/Documents/Vivado/Prototype_v2/verification/bimodal-branch-vectors/branch_%0d_%0d_%0d_%0d.mem",t,v,n,kbase),expected,0,nt*8192-1);
    expected_mac=(n+1)*nt*8*256;expect_ack(0,256,256,901,nt*512+1,1,1);branch_case=1;branch_payload=nt*256;
   end else expect_ack(3,256,256,901,0,0,0);
   send(header(18,256,256,t,901),0);beat=0;beat[12:0]=v;beat[23:16]=n+1;send(beat,0);
   for(stage=0;stage<=n;stage=stage+1)begin
    i=stage-1;kernel=stage==0?1:(kbase-1+i*2)%42+1;
    beat=0;beat[4:0]=stage;beat[15:8]=stage==0?128:17+i*10;beat[21:16]=kernel;beat[63:32]=101+stage;send(beat,0);
   end
   for(stage=0;stage<=n;stage=stage+1)begin
    i=stage-1;kernel=stage==0?1:(kbase-1+i*2)%42+1;seed=stage==0?9:11+i*5;
    zx=stage==0?128:128+i*17;dzy=stage==0?128:127-i*17;pzy=stage==0?137:128+i*13;
    for(g=0;g<16;g=g+1)for(k=0;k<256;k=k+1)begin
     beat=0;for(o=0;o<16;o=o+1)beat[o*8+:8]=wv(k,g*16+o,seed);send(beat,0);
    end
    for(g=0;g<16;g=g+1)for(o=0;o<16;o=o+1)begin
     sum=0;for(k=0;k<256;k=k+1)sum=sum+wv(k,g*16+o,seed);
     c=g*16+o;bias_value=stage==0?(c%3==0?32'h80000400:c%3==1?32'h7ffffbff:c*57-231):c*57-231;
     beat=0;beat[31:0]=(128-dzy)*sum+bias_value;beat[63:32]=32'h40000000;beat[69:64]=37;beat[79:72]=pzy;
     if(inject_bad && stage==0 && g==0 && o==0)beat[32]=1;send(beat,0);
    end
    for(c=0;c<256;c=c+1)for(part=0;part<3;part=part+1)begin
     beat=0;for(p=0;p<16;p=p+1)if(part*16+p<kernel)beat[p*8+:8]=stage==0?1:wv(part*16+p,c,seed);send(beat,0);
    end
    for(c=0;c<256;c=c+1)begin
     beat=0;beat[31:0]=stage==0?0:c*3-57;beat[63:32]=32'h40000000;beat[69:64]=stage==0?30:37;
     beat[79:72]=dzy;beat[103:96]=zx;send(beat,0);
    end
   end
   for(stage=0;stage<n;stage=stage+1)for(g=0;g<16;g=g+1)begin
    beat=0;for(p=0;p<16;p=p+1)beat[p*8+:8]=stage==0?g*16+p:((g*16+p)*3+(stage-1)*7)%256;send(beat,0);
   end
   for(c=0;c<256;c=c+1)for(tile=0;tile<nt;tile=tile+1)begin
    beat=0;for(p=0;p<16;p=p+1)beat[p*8+:8]=(tile*16+p<v)?xv(c,tile*16+p,11):128;
    send(beat,c==255 && tile==nt-1);
   end
   finish(inject_bad);
  end
 endtask
 task reset_core;
  begin @(negedge clk);rstn=0;valid=0;last=0;checking=0;repeat(3)@(negedge clk);rstn=1;end
 endtask
 task interrupt_quantizer(input reg branch);
  begin
   fork : interrupted
    begin
     if(branch)residual_case(13,7,3,11);else stream_case(17,13,2,11);
     $fatal(1,"Transaction unexpectedly completed before pipeline reset");
    end
    begin
     wait(dut.engine.pointwise.buffer_valid);
     @(negedge clk);force_stall=1;
     wait(dut.engine.pointwise.buffer_valid && !dut.engine.pointwise.quant_ready);
     if(dut.engine.pointwise.buffer_wrap!==branch)$fatal(1,"Incorrect buffered wrap mode");
     repeat(3)@(negedge clk);
     checking=0;rstn=0;valid=0;last=0;
     disable interrupted;
    end
   join
   repeat(3)@(negedge clk);
   if(dut.engine.pointwise.buffer_valid)$fatal(1,"Buffered quantizer input survived reset");
   rstn=1;force_stall=0;repeat(5)@(negedge clk);
   if(out_valid || busy || error)$fatal(1,"Stale response after quantizer reset");
  end
 endtask
 task interrupt_slice(input reg branch);
  reg [127:0] held;reg held_last;
  begin
   force dut.engine.slice_ready=1'b0;
   fork : interrupted_slice
    begin
     if(branch)residual_case(13,7,3,11);else stream_case(17,13,2,11);
     $fatal(1,"Transaction completed despite blocked stream register");
    end
    begin
     wait(dut.engine.slice_valid);@(negedge clk);
     held=dut.engine.slice_data;held_last=dut.engine.slice_last;
     repeat(5)begin
      @(negedge clk);
      if(!dut.engine.slice_valid || dut.engine.slice_data!==held || dut.engine.slice_last!==held_last)
       $fatal(1,"Stream register changed under backpressure");
     end
     checking=0;rstn=0;valid=0;last=0;disable interrupted_slice;
    end
   join
   repeat(3)@(negedge clk);
   if(dut.engine.slice_valid)$fatal(1,"Stream input survived reset");
   release dut.engine.slice_ready;
   rstn=1;force_stall=0;repeat(5)@(negedge clk);
   if(out_valid || busy || error)$fatal(1,"Stale stream response after reset");
  end
 endtask
 task interrupt_output(input reg branch);
  reg [127:0] held;reg held_last;
  begin
   // Block outer consumption, including internal load ACKs.
   // Wait for real output data rather than stopping at the first load ACK.
   // The force is applied by the monitor after the transaction begins.
   fork : interrupted_output
    begin
     if(branch)residual_case(13,7,3,11);else stream_case(17,13,2,11);
     $fatal(1,"Transaction completed despite blocked output register");
    end
    begin
     wait(dut.state==15 && dut.inner_valid);force_stall=1;@(negedge clk);
     force dut.inner_output_ready=1'b0;
     held=dut.inner_data;held_last=dut.inner_last;
     repeat(5)begin
      @(negedge clk);
      if(!dut.inner_valid || dut.inner_data!==held || dut.inner_last!==held_last)
       $fatal(1,"Output register changed under backpressure");
     end
     checking=0;rstn=0;valid=0;last=0;disable interrupted_output;
    end
   join
   repeat(3)@(negedge clk);
   if(dut.inner_valid || dut.completion_pending)$fatal(1,"Output/completion survived reset");
   release dut.inner_output_ready;
   rstn=1;force_stall=0;repeat(5)@(negedge clk);
   if(out_valid || busy || error)$fatal(1,"Stale response after output reset");
  end
 endtask
 initial begin
  test_slot=0;repeat(5)@(negedge clk);rstn=1;
  interrupt_output(1);
  residual_case(13,7,3,11);
  interrupt_output(0);
  stream_case(17,13,2,11);
  $display("PASS: 2 full output-data buffer reset/replay cases");$finish;
 end
endmodule
