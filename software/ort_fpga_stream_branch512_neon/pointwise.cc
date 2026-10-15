// Native retained depthwise/mask/pointwise custom operator. Experimental ABI.
// Physical path performs no host depthwise convolution and never falls back.
#include "include/onnxruntime_c_api.h"
extern "C" {
#include "qnc.h"
}
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>
namespace {
const OrtApi* api;
std::mutex mutex;
qnc* device=nullptr;
struct Chain;
std::vector<Chain*> physical_programs;
const void* residents[32]={};
uint64_t physical_calls=0,oracle_calls=0;
void check(OrtStatus* s){if(s){std::string e=api->GetErrorMessage(s);api->ReleaseStatus(s);throw std::runtime_error(e);}}
void require(bool yes,const char* e){if(!yes)throw std::runtime_error(e);}
std::vector<int64_t> shape(const OrtValue* v){
 OrtTensorTypeAndShapeInfo* info=nullptr;check(api->GetTensorTypeAndShape(v,&info));
 size_t n=0;OrtStatus* s=api->GetDimensionsCount(info,&n);
 if(s){api->ReleaseTensorTypeAndShapeInfo(info);check(s);}
 std::vector<int64_t> d(n);s=api->GetDimensions(info,d.data(),n);api->ReleaseTensorTypeAndShapeInfo(info);check(s);return d;
}
template<class T>const T* data(const OrtValue* v){const void* p=nullptr;check(api->GetTensorData(v,&p));return static_cast<const T*>(p);}
size_t elements(const OrtValue* v){size_t n=1;for(auto d:shape(v)){require(d>=0,"Invalid constant dimension");n*=d;}return n;}
const OrtValue* input(OrtKernelContext* c,size_t i){const OrtValue* v=nullptr;check(api->KernelContext_GetInput(c,i,&v));require(v,"Missing fused input");return v;}
struct Quant {
 std::vector<int8_t> w;
 std::vector<int32_t> bias,mult;std::vector<float> ratios;
 std::vector<uint8_t> shifts;
 uint8_t zx=0,zy=0;
 int64_t m=0,k=0,width=0;
 void init(const OrtValue* xscale,const OrtValue* xzero,const OrtValue* weight,const OrtValue* wscale,
           const OrtValue* wzero,const OrtValue* yscale,const OrtValue* yzero,const OrtValue* b,bool dw){
  auto dims=shape(weight);require(dims.size()==3,"Invalid weight rank");
  m=dims[0];k=dims[1];width=dims[2];
  require(m>0 && m<=1024 && k>0 && k<=1024 && (dw?(k==1 && width>=1 && width<=41 && (width&1)):width==1),"Unsupported fused weight shape");
  require(elements(xscale)==1 && elements(xzero)==1 && elements(yscale)==1 && elements(yzero)==1,"Quantization must be scalar");
  require(elements(wscale)==size_t(m) && (elements(wzero)==1 || elements(wzero)==size_t(m)),"Invalid weight quantization shape");
  require(shape(b)==std::vector<int64_t>{m},"Invalid bias shape");
  const float sx=*data<float>(xscale),sy=*data<float>(yscale);zx=*data<uint8_t>(xzero);zy=*data<uint8_t>(yzero);
  require(std::isfinite(sx)&&sx>0&&std::isfinite(sy)&&sy>0,"Invalid scale");
  const float* sw=data<float>(wscale);const int8_t* zw=data<int8_t>(wzero);
  w.assign(data<int8_t>(weight),data<int8_t>(weight)+m*k*width);
  bias.assign(data<int32_t>(b),data<int32_t>(b)+m);mult.resize(m);shifts.resize(m);ratios.resize(m);
  for(int64_t o=0;o<m;o++){
   require(zw[elements(wzero)==1?0:o]==0 && std::isfinite(sw[o]) && sw[o]>0,"Symmetric weight quantization required");
   float ratio=(sx*sw[o])/sy;ratios[o]=ratio;require(std::isfinite(ratio)&&ratio>0,"Invalid affine ratio");
   if(ratio<=std::ldexp(1.f,-32)){mult[o]=0;shifts[o]=1;}
   else {
    int exponent;double significand=std::frexp(double(ratio),&exponent);
    int64_t multiplier=int64_t(std::nearbyint(std::ldexp(significand,31)));
    if(multiplier==(int64_t(1)<<31)){multiplier>>=1;exponent++;}
    int shift=31-exponent;require(shift>=1&&shift<=62,"Unsupported affine shift");
    mult[o]=int32_t(multiplier);shifts[o]=uint8_t(shift);
   }
  }
 }
 uint8_t round(int64_t sum,int64_t o)const{
  require(sum>=INT32_MIN&&sum<=INT32_MAX,"Fused accumulator overflow");
  float product=static_cast<float>(sum)*ratios[o];
  product=std::clamp(product,-float(zy),255.f-float(zy));
  return static_cast<uint8_t>(static_cast<int>(std::nearbyint(product))+zy);
 }
};
struct Kernel {
 bool emulate,initialized=false;Quant dw,pw;std::vector<int32_t> corrections;
 explicit Kernel(const OrtKernelInfo* info,bool oracle):emulate(oracle){
  for(size_t i=1;i<15;i++){int constant=0;const OrtValue* v=nullptr;
   check(api->KernelInfoGetConstantInput_tensor(info,i,&constant,&v));require(constant,"Fused quantization, weights and bias must be constant");}
 }
 void compute(OrtKernelContext* c){
  const OrtValue* v[16];for(size_t i=0;i<16;i++)v[i]=input(c,i);
  auto xs=shape(v[0]);require(xs.size()==3 && xs[0]==1 && xs[2]>0 && xs[2]<=(emulate?4096:256),"Unsupported fused input shape");
  auto ms=shape(v[15]);require(ms==std::vector<int64_t>({1,1,xs[2]}),"Expected broadcast invalid-time mask");
  if(!initialized){
   dw.init(v[1],v[2],v[3],v[4],v[5],v[6],v[7],v[8],true);
   pw.init(v[6],v[7],v[9],v[10],v[11],v[12],v[13],v[14],false);
   require(pw.k==dw.m,"Depthwise/pointwise channel mismatch");
   corrections.resize(pw.m);
   for(int64_t o=0;o<pw.m;o++){
    int64_t sum=0;for(int64_t i=0;i<pw.k;i++)sum+=pw.w[o*pw.k+i];
    int64_t b=int64_t(pw.bias[o])+int64_t(128-int(pw.zx))*sum;
    require(b>=INT32_MIN&&b<=INT32_MAX,"Pointwise bias correction overflow");corrections[o]=int32_t(b);
   }
   initialized=true;
  }
  require(xs[1]==dw.m && dw.m==256 && pw.m==256 && pw.k==256,"Cache requires fixed 256-channel pairs");
  const bool* mask=data<bool>(v[15]);size_t t=size_t(xs[2]),valid_t=t;
  for(size_t p=0;p<t;p++)if(mask[p]){valid_t=p;break;}
  for(size_t p=valid_t;p<t;p++)require(mask[p],"Invalid-time mask must be a suffix");
  int64_t dims[3]={1,pw.m,int64_t(t)};OrtValue* out=nullptr;check(api->KernelContext_GetOutput(c,0,dims,3,&out));
  void* outptr=nullptr;check(api->GetTensorMutableData(out,&outptr));auto y=static_cast<uint8_t*>(outptr);
  const uint8_t* x=data<uint8_t>(v[0]);
  std::lock_guard<std::mutex> lock(mutex);
  if(emulate){
   std::vector<uint8_t> intermediate(dw.m*t);
   for(int64_t channel=0;channel<dw.m;channel++)for(size_t p=0;p<t;p++){
    int64_t sum=dw.bias[channel];
    for(int tap=0;tap<dw.width;tap++){int64_t idx=int64_t(p)+tap-dw.width/2;
     if(idx>=0&&idx<int64_t(t))sum+=int64_t(int(x[channel*t+idx])-int(dw.zx))*dw.w[channel*dw.width+tap];}
    uint8_t q=dw.round(sum,channel);intermediate[channel*t+p]=p<valid_t?q:dw.zy;
   }
   for(int64_t o=0;o<pw.m;o++)for(size_t p=0;p<t;p++){
    int64_t sum=pw.bias[o];for(int64_t i=0;i<pw.k;i++)sum+=int64_t(int(intermediate[i*t+p])-int(pw.zx))*pw.w[o*pw.k+i];
    y[o*t+p]=pw.round(sum,o);
   }
   oracle_calls++;
  }else{
   if(!device){
    std::ifstream version("/sys/module/qn_tile/parameters/short_cache_protocol");unsigned protocol=0;
    require(bool(version>>protocol)&&protocol==1,"Matching retained-pair driver required");
    device=qnc_open("/dev/qn-tile");require(device,"Cannot open physical FPGA");
   }
   unsigned slot=32;
   for(unsigned i=0;i<32;i++)if(residents[i]==this){slot=i;break;}
   bool needs_load=slot==32;
   if(needs_load){for(unsigned i=0;i<32;i++)if(!residents[i]){slot=i;break;}}
   require(slot<32,"At most thirty-two resident physical kernels per library are supported");
   int rc=qnc_select_slot(device,slot);
   if(!rc && needs_load){
    rc=qnc_load_quant(device,pw.w.data(),unsigned(pw.m),unsigned(pw.k),corrections.data(),pw.mult.data(),pw.shifts.data(),pw.zy);
    if(!rc)rc=qnc_load_depthwise(device,dw.w.data(),dw.bias.data(),dw.mult.data(),dw.shifts.data(),dw.zx,dw.zy,unsigned(dw.width));
    if(!rc)residents[slot]=this;
   }
   if(!rc)rc=qnc_run_pair(device,x,t,valid_t,y);
   if(rc){for(auto& owner:residents)owner=nullptr;throw std::runtime_error("Cached FPGA transport failed: "+std::to_string(rc));}
   physical_calls++;
  }
 }
};

struct Stage {Quant dw,pw;std::vector<int32_t> corrections;uint8_t lookup[256],masked=0;};
template<class T>T read_value(const uint8_t*& p,const uint8_t* end){
 require(size_t(end-p)>=sizeof(T),"Truncated chain program");T v;std::memcpy(&v,p,sizeof(v));p+=sizeof(v);return v;
}
void raw_quant(Quant& q,float sx,float sy,const std::vector<float>& sw){
 require(std::isfinite(sx)&&sx>0&&std::isfinite(sy)&&sy>0,"Invalid chain scale");
 q.mult.resize(256);q.shifts.resize(256);q.ratios.resize(256);
 for(int o=0;o<256;o++){
  require(std::isfinite(sw[o])&&sw[o]>0,"Invalid per-channel scale");
  float ratio=(sx*sw[o])/sy;q.ratios[o]=ratio;require(std::isfinite(ratio)&&ratio>0,"Invalid chain affine ratio");
  if(ratio<=std::ldexp(1.f,-32)){q.mult[o]=0;q.shifts[o]=1;}
  else {int exponent;double significand=std::frexp(double(ratio),&exponent);
   int64_t multiplier=int64_t(std::nearbyint(std::ldexp(significand,31)));
   if(multiplier==(int64_t(1)<<31)){multiplier>>=1;exponent++;}
   int shift=31-exponent;require(shift>=1&&shift<=62,"Unsupported chain affine shift");
   q.mult[o]=int32_t(multiplier);q.shifts[o]=uint8_t(shift);
  }
 }
}
struct Chain {
 bool emulate,branch;int64_t residual_stride=1;std::vector<Stage> stages;std::vector<uint8_t> prefix;size_t prefix_bytes=0;uint32_t outer_tag=1;uint64_t registered_handle=0;
 Chain(const OrtKernelInfo* info,bool oracle,bool residual_branch=false):emulate(oracle),branch(residual_branch){
  if(branch){check(api->KernelInfoGetAttribute_int64(info,"residual_stride",&residual_stride));require(residual_stride==1||residual_stride==2,"Unsupported residual stride");}
  int constant=0;const OrtValue* value=nullptr;
  check(api->KernelInfoGetConstantInput_tensor(info,2,&constant,&value));require(constant,"Chain program must be constant");
  const uint8_t* p=data<uint8_t>(value);const uint8_t* end=p+elements(value);
  require(read_value<uint32_t>(p,end)==0x314e4843,"Invalid chain program magic");
  unsigned count=read_value<uint32_t>(p,end);require(count>0&&count<=8&&(!branch||count>=2),"Invalid chain length");stages.resize(count);
  for(auto& s:stages){
   unsigned kernel=read_value<uint32_t>(p,end);require(kernel>=1&&kernel<=41&&(kernel&1),"Invalid chain kernel");
   s.dw.m=256;s.dw.k=1;s.dw.width=kernel;s.pw.m=256;s.pw.k=256;s.pw.width=1;
   auto zero=[&](){unsigned z=read_value<uint32_t>(p,end);require(z<=255,"Invalid chain zero point");return uint8_t(z);};
   s.dw.zx=zero();s.dw.zy=zero();s.pw.zx=s.dw.zy;s.pw.zy=zero();
   float sx=read_value<float>(p,end),sd=read_value<float>(p,end),sy=read_value<float>(p,end);
   std::vector<float> dwscale(256),pwscale(256);
   for(auto& x:dwscale)x=read_value<float>(p,end);for(auto& x:pwscale)x=read_value<float>(p,end);
   s.dw.bias.resize(256);s.pw.bias.resize(256);
   for(auto& x:s.dw.bias)x=read_value<int32_t>(p,end);for(auto& x:s.pw.bias)x=read_value<int32_t>(p,end);
   s.dw.w.resize(256*kernel);s.pw.w.resize(65536);
   for(auto& x:s.dw.w)x=read_value<int8_t>(p,end);for(auto& x:s.pw.w)x=read_value<int8_t>(p,end);
   for(auto& x:s.lookup)x=read_value<uint8_t>(p,end);s.masked=zero();
   raw_quant(s.dw,sx,sd,dwscale);raw_quant(s.pw,sd,sy,pwscale);s.corrections.resize(256);
   for(int o=0;o<256;o++){
    int64_t sum=0;for(int i=0;i<256;i++)sum+=s.pw.w[o*256+i];
    int64_t b=int64_t(s.pw.bias[o])+int64_t(128-int(s.pw.zx))*sum;
    if(branch && &s==stages.data()){uint32_t bits=uint32_t(uint64_t(b));std::memcpy(&s.corrections[o],&bits,sizeof(bits));}
    else {require(b>=INT32_MIN&&b<=INT32_MAX,"Chain correction overflow");s.corrections[o]=int32_t(b);}
   }
  }
  require(p==end,"Trailing chain program bytes");
  if(!emulate){
   const auto* raw=data<uint8_t>(value);uint32_t hash=2166136261u;
   for(size_t i=0;i<elements(value);i++){hash^=raw[i];hash*=16777619u;}
   outer_tag=hash?hash:1;
   prefix_bytes=32+count*16+count*86016+(count-1)*256;prefix.resize(prefix_bytes+65536,0);
   auto put=[&](size_t offset,uint32_t word){for(unsigned b=0;b<4;b++)prefix[offset+b]=uint8_t(word>>(8*b));};
   put(0,0x32434e51);put(4,(branch?18:17)|(256u<<16));put(8,256);put(12,outer_tag);put(16,count<<16);
   for(unsigned i=0;i<count;i++){
    auto& s=stages[i];uint32_t tag=outer_tag+i*0x9e3779b9u;if(!tag)tag=1;
    put(32+i*16,i|(uint32_t(s.masked)<<8)|(uint32_t(s.dw.width)<<16));put(36+i*16,tag);
    size_t base=32+count*16+i*86016;
    for(unsigned g=0;g<16;g++)for(unsigned k=0;k<256;k++)for(unsigned lane=0;lane<16;lane++)
     prefix[base+(g*256+k)*16+lane]=uint8_t(s.pw.w[(g*16+lane)*256+k]);
    for(unsigned c=0;c<256;c++){
     size_t pw=base+65536+c*16;
     put(pw,uint32_t(s.corrections[c]));put(pw+4,uint32_t(s.pw.mult[c]));put(pw+8,s.pw.shifts[c]|(uint32_t(s.pw.zy)<<8));
     std::memcpy(prefix.data()+base+69632+c*48,s.dw.w.data()+c*s.dw.width,size_t(s.dw.width));
     size_t dp=base+69632+12288+c*16;
     put(dp,uint32_t(s.dw.bias[c]));put(dp+4,uint32_t(s.dw.mult[c]));put(dp+8,s.dw.shifts[c]|(uint32_t(s.dw.zy)<<8));put(dp+12,s.dw.zx);
    }
    if(i+1<count)std::memcpy(prefix.data()+32+count*16+count*86016+i*256,s.lookup,256);
   }
   std::lock_guard<std::mutex> guard(mutex);physical_programs.push_back(this);
  }

 }
 void compute(OrtKernelContext* context){
  const OrtValue* xv=input(context,0);const OrtValue* mv=input(context,1);auto dims=shape(xv);
  require(dims.size()==3&&dims[0]==1&&dims[1]==256&&dims[2]>0&&dims[2]<=(emulate?4096:256),"Invalid chain activation shape");
  size_t t=size_t(dims[2]),valid_t=t;require(shape(mv)==std::vector<int64_t>{1,1,int64_t(t)},"Invalid chain mask shape");
  auto mask=data<bool>(mv);for(size_t p=0;p<t;p++)if(mask[p]){valid_t=p;break;}
  for(size_t p=valid_t;p<t;p++)require(mask[p],"Chain mask must be a suffix");
  OrtValue* output=nullptr;check(api->KernelContext_GetOutput(context,0,dims.data(),3,&output));
  void* outptr=nullptr;check(api->GetTensorMutableData(output,&outptr));auto y=static_cast<uint8_t*>(outptr);
  uint8_t* residual=nullptr;
  if(branch){auto rdims=dims;rdims[2]=(dims[2]+residual_stride-1)/residual_stride;OrtValue* value=nullptr;check(api->KernelContext_GetOutput(context,1,rdims.data(),3,&value));void* pointer=nullptr;check(api->GetTensorMutableData(value,&pointer));residual=static_cast<uint8_t*>(pointer);}
  auto x=data<uint8_t>(xv);std::lock_guard<std::mutex> lock(mutex);
  if(emulate){
   std::vector<uint8_t> a(x,x+256*t),b(256*t),intermediate(256*t);
   for(size_t stage=0;stage<stages.size();stage++){
    auto& s=stages[stage];
    for(int c=0;c<256;c++)for(size_t p=0;p<t;p++){
     int64_t sum=s.dw.bias[c];for(int tap=0;tap<s.dw.width;tap++){
      int64_t index=int64_t(p)+tap-s.dw.width/2;
      if(index>=0&&index<int64_t(t))sum+=int64_t(int(a[c*t+index])-int(s.dw.zx))*s.dw.w[c*s.dw.width+tap];
     }
     intermediate[c*t+p]=p<valid_t?s.dw.round(sum,c):s.dw.zy;
    }
    for(int o=0;o<256;o++)for(size_t p=0;p<t;p++){
     int64_t sum=s.pw.bias[o];for(int c=0;c<256;c++)sum+=int64_t(int(intermediate[c*t+p])-int(s.pw.zx))*s.pw.w[o*256+c];
     if(branch&&stage==0){uint32_t bits=uint32_t(uint64_t(sum));int32_t wrapped;std::memcpy(&wrapped,&bits,sizeof(bits));sum=wrapped;}
     uint8_t q=s.pw.round(sum,o);
     b[o*t+p]=(branch&&stage==0)||stage+1==stages.size()?q:p>=valid_t?s.masked:s.lookup[q];
    }
    if(branch&&stage==0){size_t rt=(t+residual_stride-1)/residual_stride;for(size_t c=0;c<256;c++)for(size_t p=0;p<rt;p++)residual[c*rt+p]=b[c*t+p*residual_stride];}else a.swap(b);
   }
   std::memcpy(y,a.data(),256*t);oracle_calls+=stages.size();
  }else{

   if(!device){
    std::ifstream version("/sys/module/qn_tile/parameters/residual_branch_protocol");unsigned protocol=0;
    require(bool(version>>protocol)&&protocol==1,"Matching residual branch driver required");
    device=qnc_open("/dev/qn-tile");require(device,"Cannot open physical streamed FPGA");
   }
   if(!registered_handle){
    int rc=qnc_register_stream(device,prefix.data(),prefix_bytes,&registered_handle);
    if(rc)throw std::runtime_error("FPGA program registration failed: "+std::to_string(rc));
   }
   int rc=branch ? qnc_run_branch_registered(device,registered_handle,prefix.data(),prefix_bytes,prefix.size(),unsigned(stages.size()),outer_tag,x,t,valid_t,y,residual,unsigned(residual_stride)) : qnc_run_registered(device,registered_handle,prefix.data(),prefix_bytes,prefix.size(),unsigned(stages.size()),outer_tag,x,t,valid_t,y);
   if(rc)throw std::runtime_error("Streamed FPGA transport failed: "+std::to_string(rc));
   physical_calls+=stages.size();
  }
 }
};
OrtCustomOp physical={},oracle={},physical_branch={},oracle_branch={};
OrtStatus* ORT_API_CALL create(const OrtCustomOp* op,const OrtApi* a,const OrtKernelInfo* info,void** out){
 api=a;try{*out=new Chain(info,op==&oracle||op==&oracle_branch,op==&physical_branch||op==&oracle_branch);return nullptr;}catch(const std::exception& e){return a->CreateStatus(ORT_FAIL,e.what());}}
OrtStatus* ORT_API_CALL compute(void* p,OrtKernelContext* c){try{static_cast<Chain*>(p)->compute(c);return nullptr;}catch(const std::exception& e){return api->CreateStatus(ORT_FAIL,e.what());}}
void ORT_API_CALL destroy(void* p){std::lock_guard<std::mutex> lock(mutex);auto chain=static_cast<Chain*>(p);
 if(chain->registered_handle && device)qnc_release_stream(device,chain->registered_handle);
 for(auto& s:chain->stages)for(auto& owner:residents)if(owner==&s)owner=nullptr;
 physical_programs.erase(std::remove(physical_programs.begin(),physical_programs.end(),chain),physical_programs.end());delete chain;}
const char* ORT_API_CALL name(const OrtCustomOp* p){return p==&oracle_branch?"OracleBranch":p==&physical_branch?"FpgaBranch":p==&oracle?"OracleChain":"FpgaChain";}
const char* ORT_API_CALL provider(const OrtCustomOp*){return "CPUExecutionProvider";}
size_t ORT_API_CALL inputs(const OrtCustomOp*){return 3;}
size_t ORT_API_CALL outputs(const OrtCustomOp* p){return p==&physical_branch||p==&oracle_branch?2:1;}
ONNXTensorElementDataType ORT_API_CALL input_type(const OrtCustomOp*,size_t i){return i==1?ONNX_TENSOR_ELEMENT_DATA_TYPE_BOOL:ONNX_TENSOR_ELEMENT_DATA_TYPE_UINT8;}
ONNXTensorElementDataType ORT_API_CALL output_type(const OrtCustomOp*,size_t){return ONNX_TENSOR_ELEMENT_DATA_TYPE_UINT8;}
OrtCustomOpInputOutputCharacteristic ORT_API_CALL characteristic(const OrtCustomOp*,size_t){return INPUT_OUTPUT_REQUIRED;}
OrtMemType ORT_API_CALL memory(const OrtCustomOp*,size_t){return OrtMemTypeDefault;}
std::vector<OrtCustomOpDomain*> domains;
}
extern "C" ORT_EXPORT OrtStatus* ORT_API_CALL RegisterCustomOps(OrtSessionOptions* options,const OrtApiBase* base){
 api=base->GetApi(ORT_API_VERSION);if(!api)return nullptr;
 for(auto op:{&physical,&oracle,&physical_branch,&oracle_branch}){
  *op={};op->version=ORT_API_VERSION;op->GetName=name;op->GetExecutionProviderType=provider;
  op->GetInputTypeCount=inputs;op->GetOutputTypeCount=outputs;op->GetInputType=input_type;op->GetOutputType=output_type;
  op->GetInputCharacteristic=characteristic;op->GetOutputCharacteristic=characteristic;op->GetInputMemoryType=memory;
  op->CreateKernelV2=create;op->KernelComputeV2=compute;op->KernelDestroy=destroy;
 }
 OrtCustomOpDomain* domain=nullptr;OrtStatus* s=api->CreateCustomOpDomain("prototype.fpga.stream.branch512",&domain);if(s)return s;
 for(auto op:{&physical,&oracle,&physical_branch,&oracle_branch}){if(!s)s=api->CustomOpDomain_Add(domain,op);}
 if(!s)s=api->AddCustomOpDomain(options,domain);
 if(s){api->ReleaseCustomOpDomain(domain);return s;}domains.push_back(domain);return nullptr;
}
extern "C" void fpga_ort_stats(qnc_stats* out,uint64_t* calls,uint64_t* emulated){std::lock_guard<std::mutex> lock(mutex);*out={};if(device)qnc_get_stats(device,out);*calls=physical_calls;*emulated=oracle_calls;}
extern "C" void fpga_ort_profile(qnc_profile* out){std::lock_guard<std::mutex> lock(mutex);*out={};if(device)qnc_get_profile(device,out);}
// Inspect prepacked programs without running the graph or opening a device.
extern "C" size_t fpga_stream_prefix_count(){std::lock_guard<std::mutex> lock(mutex);return physical_programs.size();}
extern "C" int64_t fpga_stream_copy_prefix(unsigned index,uint8_t* out,size_t capacity,unsigned* count,unsigned* tag){
 std::lock_guard<std::mutex> lock(mutex);
 if(index>=physical_programs.size())return -1;
 auto* chain=physical_programs[index];*count=unsigned(chain->stages.size());*tag=chain->outer_tag;
 if(!out)return int64_t(chain->prefix_bytes);
 if(capacity<chain->prefix_bytes)return -2;
 std::memcpy(out,chain->prefix.data(),chain->prefix_bytes);return int64_t(chain->prefix_bytes);
}
