#include "Vstt_engine.h"
#include "Vstt_engine___024root.h"
#include "verilated.h"
#include <fstream>
#include <iostream>
#include <vector>
#include <random>
#include <stdexcept>
#include <cstdint>
static std::mt19937 rng(901);
static uint64_t clocks=0;
static bool no_stalls=false;
static uint32_t read32(std::ifstream &f){uint32_t x; f.read((char*)&x,4);if(!f)throw std::runtime_error("short stimuli");return x;}
static std::vector<uint8_t> bytes(std::ifstream &f,size_t n){std::vector<uint8_t>x(n);f.read((char*)x.data(),n);if(!f)throw std::runtime_error("short tensor");return x;}
static void word(std::vector<uint8_t>&v,uint32_t x){for(int i=0;i<4;i++)v.push_back(uint8_t(x>>(8*i)));}
static uint32_t wordat(const std::vector<uint8_t>&v,size_t p){uint32_t x=0;for(int i=0;i<4;i++)x|=uint32_t(v.at(p+i))<<(8*i);return x;}
static void pad(std::vector<uint8_t>&v){while(v.size()%16)v.push_back(0);}
static void tick(Vstt_engine &d){d.aclk=0;d.eval();d.aclk=1;d.eval();clocks++;}
static std::vector<uint8_t> transact(Vstt_engine &d,const std::vector<uint8_t>&cmd,bool send_last=true,bool bad_keep=false){
 size_t sent=0;std::vector<uint8_t> out; bool holding=false, stalled=false;uint32_t old[4]={};bool last=false;
 for(uint64_t c=0;c<600000000;c++){
  d.aclk=0;
  if(!holding){d.s_axis_tvalid=sent<cmd.size() && (no_stalls || rng()%5!=0);for(int i=0;i<4;i++)d.s_axis_tdata[i]=sent<cmd.size()?wordat(cmd,sent+4*i):0;d.s_axis_tlast=send_last && sent+16==cmd.size();d.s_axis_tkeep=bad_keep?0x7fff:0xffff;}
  d.m_axis_tready=no_stalls || rng()%4!=0;d.eval();
  holding=d.s_axis_tvalid&&!d.s_axis_tready;
  bool sf=d.s_axis_tvalid&&d.s_axis_tready;
  if(stalled){if(!d.m_axis_tvalid||last!=bool(d.m_axis_tlast))throw std::runtime_error("unstable output control");for(int i=0;i<4;i++)if(old[i]!=d.m_axis_tdata[i])throw std::runtime_error("unstable output data");}
  stalled=d.m_axis_tvalid&&!d.m_axis_tready;for(int i=0;i<4;i++)old[i]=d.m_axis_tdata[i];last=d.m_axis_tlast;
  bool finish=d.m_axis_tvalid&&d.m_axis_tready&&d.m_axis_tlast;
  if(d.m_axis_tvalid&&d.m_axis_tready){if(d.m_axis_tkeep!=65535)throw std::runtime_error("TKEEP");for(int i=0;i<4;i++)word(out,d.m_axis_tdata[i]);}
  d.aclk=1;d.eval();clocks++;if(sf)sent+=16;
  if(finish){if(sent!=cmd.size())throw std::runtime_error("early response");return out;}
 }
 throw std::runtime_error("command timeout sent="+std::to_string(sent));
}
static void write_reg(Vstt_engine &d,unsigned addr,uint32_t data,bool data_first=false,unsigned strb=15){
 d.s_axil_awaddr=addr;d.s_axil_wdata=data;d.s_axil_wstrb=strb;
 bool aw=false,w=false;
 for(unsigned c=0;c<100;c++){
  d.aclk=0;d.s_axil_awvalid=!aw&&(!data_first||c>=3);d.s_axil_wvalid=!w&&(data_first||c>=3);d.eval();
  bool af=d.s_axil_awvalid&&d.s_axil_awready,wf=d.s_axil_wvalid&&d.s_axil_wready,done=d.s_axil_bvalid;
  tick(d);aw|=af;w|=wf;
  if(done){d.s_axil_awvalid=0;d.s_axil_wvalid=0;return;}
 }
 throw std::runtime_error("AXI write timeout");
}
static uint32_t read_reg(Vstt_engine &d,unsigned addr){
 d.s_axil_araddr=addr;d.s_axil_arvalid=1;
 for(unsigned c=0;c<100;c++){
  d.aclk=0;d.eval();bool af=d.s_axil_arvalid&&d.s_axil_arready,done=d.s_axil_rvalid;uint32_t value=d.s_axil_rdata;
  tick(d);if(af)d.s_axil_arvalid=0;if(done)return value;
 }
 throw std::runtime_error("AXI read timeout");
}
static void protocol_tests(Vstt_engine &d){
 if(read_reg(d,8)!=0x10002)throw std::runtime_error("version register");
 write_reg(d,0x38,0xaabbccdd);write_reg(d,0x38,0x11223344,true,5);
 if(read_reg(d,0x38)!=0xaa22cc44)throw std::runtime_error("WSTRB or independent AW/W");
 write_reg(d,0x0c,3);
 unsigned tests=0;
 for(unsigned kind=0;kind<6;kind++){
  std::vector<uint8_t> cmd;uint32_t h[]={0x32434e51,1,0,0x12340000+kind,0,0,0,0,0,0,0,0,0,0,0,0};
  unsigned expected=0;
  if(kind==1){h[0]=0;expected=1;}if(kind==2){h[2]=99;expected=2;}
  if(kind==3){h[4]=16;expected=3;}if(kind==4){h[1]=2;expected=1;}
  if(kind==5){h[2]=1;h[5]=1025;expected=4;}
  for(auto v:h)word(cmd,v);
  auto out=transact(d,cmd);
  if(out.size()!=64||wordat(out,8)!=expected||wordat(out,12)!=h[3])throw std::runtime_error("protocol case "+std::to_string(kind));
  for(int i=0;i<8;i++)tick(d);
  if(!d.irq)throw std::runtime_error("completion IRQ");
  write_reg(d,0x10,3,true);for(int i=0;i<3;i++)tick(d);
  if(d.irq){throw std::runtime_error("IRQ W1C");}
  tests++;
 }
 for(unsigned n=1;n<=3;n++){
  std::vector<uint8_t>cmd(n*16,0);auto out=transact(d,cmd);
  if(wordat(out,8)!=3){throw std::runtime_error("truncated header");}
  tests++;
 }
 std::vector<uint8_t>ping;uint32_t h[]={0x32434e51,1,0,456,0,0,0,0,0,0,0,0,0,0,0,0};for(auto v:h)word(ping,v);
 auto bad=transact(d,ping,true,true);if(wordat(bad,8)!=3)throw std::runtime_error("bad TKEEP");tests++;
 write_reg(d,0x1c,32);
 std::vector<uint8_t>partial(ping.begin(),ping.begin()+16);
 auto timeout=transact(d,partial,false);if(wordat(timeout,8)!=6)throw std::runtime_error("watchdog");tests++;
 write_reg(d,0x1c,0x1000000);
 auto recovery=transact(d,ping);if(wordat(recovery,8)!=0)throw std::runtime_error("timeout recovery");tests++;
 write_reg(d,0,1);for(int i=0;i<8;i++)tick(d);
 if(d.irq)throw std::runtime_error("soft reset IRQ");
 auto reset=transact(d,ping);if(wordat(reset,8)!=0)throw std::runtime_error("soft reset recovery");tests++;
 std::cout<<"PASS protocol/register/IRQ cases: "<<tests<<std::endl;
}
int main(int argc,char**argv){Verilated::commandArgs(argc,argv);no_stalls=argc>1 && (std::string(argv[1])=="t150" || std::string(argv[1])=="dw150" || std::string(argv[1])=="add150");try{
 Vstt_engine d;d.aresetn=0;d.s_axis_tvalid=0;d.s_axis_tkeep=65535;d.m_axis_tready=0;d.s_axil_awvalid=0;d.s_axil_wvalid=0;d.s_axil_arvalid=0;d.s_axil_bready=1;d.s_axil_rready=1;
 for(int i=0;i<5;i++){tick(d);}
 d.aresetn=1;
 for(int i=0;i<5;i++){tick(d);}
 protocol_tests(d);
 if(argc>1 && (std::string(argv[1])=="network" || std::string(argv[1])=="add" || std::string(argv[1])=="dw" || std::string(argv[1])=="dw150" || std::string(argv[1])=="add150")){
  bool add=std::string(argv[1]).rfind("add",0)==0;
  std::ifstream f(std::string(argv[1])=="network"?"network.bin":add?(no_stalls?"add150.bin":"add.bin"):(no_stalls?"depthwise150.bin":"depthwise.bin"),std::ios::binary);
  unsigned count=read32(f); std::ofstream csv(std::string(argv[1])=="network"?"network_cycles.csv":add?(no_stalls?"add150_cycles.csv":"add_cycles.csv"):(no_stalls?"depthwise150_cycles.csv":"depthwise_cycles.csv"));
  csv<<"name,cycles\n";
  for(unsigned i=0;i<count;i++){
   unsigned nl=read32(f),nc=read32(f),ny=read32(f),status=read32(f),tout=read32(f),valid=read32(f);
   auto label=bytes(f,nl);std::string name(label.begin(),label.end());
   auto cmd=bytes(f,nc),y=bytes(f,ny);
   if(std::string(argv[1])=="network") no_stalls=name.find("T32/")==std::string::npos;
   auto out=transact(d,cmd);
   if(std::string(argv[1])=="network") std::cout<<"CYCLES "<<name<<" total="<<wordat(out,16)
       <<" compute="<<d.rootp->stt_engine__DOT__network_compute_cycles
       <<" weight="<<d.rootp->stt_engine__DOT__network_weight_cycles
       <<" transition="<<d.rootp->stt_engine__DOT__network_transition_cycles<<std::endl;
   if(wordat(out,8)!=status)throw std::runtime_error(name+" status "+std::to_string(wordat(out,8))+" error beat "+std::to_string(wordat(out,56)));
   if(!status){
    if(wordat(out,40)!=tout||wordat(out,44)!=valid)throw std::runtime_error(name+" lengths");
    if(out.size()!=64+(ny+15)/16*16)throw std::runtime_error(name+" size");
    for(unsigned j=0;j<ny;j++)if(out[64+j]!=y[j])throw std::runtime_error(name+" byte "+std::to_string(j)+" got "+std::to_string(out[64+j])+" expected "+std::to_string(y[j]));
   }
   csv<<name<<","<<wordat(out,16)<<"\n";std::cout<<"PASS "<<name<<std::endl;
  }
  std::cout<<"PASS streamed commands "<<count<<" cases\n";return 0;
 }
 std::ifstream f(argc>2?argv[2]:"layers.bin",std::ios::binary);unsigned count=read32(f),passed=0;uint64_t checked=0;
 std::ofstream csv(no_stalls?"engine_t150_cycles.csv":"engine_cycles.csv");csv<<"name,ci,co,t,total_cycles,reported_cycles,issue_cycles,status\n";
 for(unsigned test=0;test<count;test++){
  unsigned ci=read32(f),co=read32(f),t=read32(f),valid=read32(f),sign=read32(f),relu=read32(f),len=read32(f),overflow=read32(f);read32(f);
  auto label=bytes(f,len);std::string name(label.begin(),label.end());
  std::vector<uint8_t>p[3];for(auto &v:p)v=bytes(f,co*4);
  auto w=bytes(f,ci*co),x=bytes(f,ci*t),y=bytes(f,co*t);
  if(no_stalls && name.rfind("T150/",0)!=0){continue;}
  std::vector<uint8_t>payload=x;pad(payload);
  unsigned kpt=(ci+15)/16,ot=(co+31)/32,nb=(co+3)/4,record=64+48*nb+ot*kpt*512;
  uint32_t rec[]={0x32524c51,record,1,1,0,0,relu,co,ci,1,1,1,0,1,0,0};for(auto v:rec)word(payload,v);
  for(auto &v:p){payload.insert(payload.end(),v.begin(),v.end());pad(payload);}
  for(unsigned o=0;o<ot;o++)for(unsigned k=0;k<kpt*16;k++)for(unsigned l=0;l<32;l++)payload.push_back(o*32+l<co&&k<ci?w[(o*32+l)*ci+k]:0);
  std::vector<uint8_t>cmd;uint32_t h[]={0x32434e51,1,1,test,unsigned(payload.size()),ci,t,valid,co,sign,0,0,0,0,0,0};for(auto v:h)word(cmd,v);cmd.insert(cmd.end(),payload.begin(),payload.end());
  uint64_t begin=clocks;auto out=transact(d,cmd);unsigned status=wordat(out,8);
  if(status!=(overflow?5u:0u))throw std::runtime_error(name+" status="+std::to_string(status));
  if(wordat(out,0)!=0x32534e51||wordat(out,12)!=test)throw std::runtime_error("response header");
  if(!overflow){if(out.size()!=64+(y.size()+15)/16*16)throw std::runtime_error("output length");for(size_t i=0;i<y.size();i++)if(out[64+i]!=y[i])throw std::runtime_error(name+" mismatch byte="+std::to_string(i)+" got="+std::to_string(out[64+i])+" expected="+std::to_string(y[i]));checked+=y.size();}
  csv<<name<<","<<ci<<","<<co<<","<<t<<","<<clocks-begin<<","<<wordat(out,16)<<","<<wordat(out,28)<<","<<status<<"\n";
  std::cout<<"PASS "<<name<<" cycles="<<clocks-begin<<" reported="<<wordat(out,16)<<std::endl;passed++;
 }
 std::cout<<"PASS full engine: "<<passed<<" layers, "<<checked<<" output bytes\n";
 }catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<std::endl;return 1;}}
