// CPU-only masked pooling. ORT retains the original float reduction order.
#include "../ort_fpga_shortwide/include/onnxruntime_c_api.h"
#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>
namespace {
const OrtApi* api;
std::atomic<uint64_t> calls{0};
void check(OrtStatus* s){if(s){std::string text=api->GetErrorMessage(s);api->ReleaseStatus(s);throw std::runtime_error(text);}}
std::vector<int64_t> shape(const OrtValue* v){
 OrtTensorTypeAndShapeInfo* i=nullptr;check(api->GetTensorTypeAndShape(v,&i));size_t n=0;
 auto s=api->GetDimensionsCount(i,&n);if(s){api->ReleaseTensorTypeAndShapeInfo(i);check(s);}
 std::vector<int64_t> result(n);s=api->GetDimensions(i,result.data(),n);api->ReleaseTensorTypeAndShapeInfo(i);check(s);return result;
}
template<class T>const T* data(const OrtValue* v){const void* p=nullptr;check(api->GetTensorData(v,&p));return static_cast<const T*>(p);}
struct Kernel {
 OrtOp *sum=nullptr,*divide=nullptr;OrtMemoryInfo* memory=nullptr;
 explicit Kernel(const OrtKernelInfo* info){
  int constant=0;const OrtValue* axes=nullptr;check(api->KernelInfoGetConstantInput_tensor(info,2,&constant,&axes));
  if(!constant || shape(axes)!=std::vector<int64_t>{1} || (*data<int64_t>(axes)!=2 && *data<int64_t>(axes)!=-1))
   throw std::runtime_error("SE reduction axis must be constant [2] or [-1]");
  try{
   check(api->CreateCpuMemoryInfo(OrtArenaAllocator,OrtMemTypeDefault,&memory));
   const char* names[]={"T"};ONNXTensorElementDataType types[]={ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT};
   int64_t keep=1;OrtOpAttr* attr=nullptr;check(api->CreateOpAttr("keepdims",&keep,1,ORT_OP_ATTR_INT,&attr));
   auto s=api->CreateOp(info,"ReduceSum","",13,names,types,1,&attr,1,2,1,&sum);api->ReleaseOpAttr(attr);check(s);
   check(api->CreateOp(info,"Div","",13,names,types,1,nullptr,0,2,1,&divide));
  }catch(...){release();throw;}
 }
 void release(){if(sum)api->ReleaseOp(sum);if(divide)api->ReleaseOp(divide);if(memory)api->ReleaseMemoryInfo(memory);sum=nullptr;divide=nullptr;memory=nullptr;}
 ~Kernel(){release();}
 void compute(OrtKernelContext* context){
  const OrtValue *xv=nullptr,*maskv=nullptr,*axes=nullptr;
  check(api->KernelContext_GetInput(context,0,&xv));check(api->KernelContext_GetInput(context,1,&maskv));check(api->KernelContext_GetInput(context,2,&axes));
  auto dims=shape(xv);if(dims.size()!=3 || dims[0]!=1 || dims[1]<=0 || dims[2]<=0 || dims[2]>4096 ||
      shape(maskv)!=std::vector<int64_t>{1,1,dims[2]})throw std::runtime_error("Invalid SE pooling shapes");
  size_t t=size_t(dims[2]),c=size_t(dims[1]),valid=0;bool suffix=true,invalid=false;
  auto mask=data<bool>(maskv);auto x=data<float>(xv);
  for(size_t p=0;p<t;p++){if(mask[p])invalid=true;else {valid++;if(invalid)suffix=false;}}
  OrtValue *masked=nullptr,*mean=nullptr;
  check(api->KernelContext_GetOutput(context,0,dims.data(),3,&masked));
  int64_t md[3]={1,dims[1],1};check(api->KernelContext_GetOutput(context,1,md,3,&mean));
  void* pointer=nullptr;check(api->GetTensorMutableData(masked,&pointer));auto out=static_cast<float*>(pointer);
  if(suffix){
   for(size_t channel=0;channel<c;channel++){
    std::memcpy(out+channel*t,x+channel*t,valid*sizeof(float));
    std::fill(out+channel*t+valid,out+(channel+1)*t,0.f);
   }
  }else for(size_t channel=0;channel<c;channel++)for(size_t p=0;p<t;p++)out[channel*t+p]=mask[p]?0.f:x[channel*t+p];
  const OrtValue* sum_inputs[]={masked,axes};check(api->InvokeOp(context,sum,sum_inputs,2,&mean,1));
  float denominator=float(valid);int64_t dd[3]={1,1,1};OrtValue* den=nullptr;
  check(api->CreateTensorWithDataAsOrtValue(memory,&denominator,sizeof(denominator),dd,3,ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT,&den));
  const OrtValue* div_inputs[]={mean,den};auto status=api->InvokeOp(context,divide,div_inputs,2,&mean,1);
  api->ReleaseValue(den);check(status);calls.fetch_add(1,std::memory_order_relaxed);
 }
};
OrtCustomOp op={};std::vector<OrtCustomOpDomain*> domains;
OrtStatus* ORT_API_CALL create(const OrtCustomOp*,const OrtApi* a,const OrtKernelInfo* i,void** out){api=a;try{*out=new Kernel(i);return nullptr;}catch(const std::exception& e){return a->CreateStatus(ORT_FAIL,e.what());}}
OrtStatus* ORT_API_CALL compute(void* p,OrtKernelContext* c){try{static_cast<Kernel*>(p)->compute(c);return nullptr;}catch(const std::exception& e){return api->CreateStatus(ORT_FAIL,e.what());}}
void ORT_API_CALL destroy(void* p){delete static_cast<Kernel*>(p);}
const char* ORT_API_CALL name(const OrtCustomOp*){return "MaskedSEMean";}
const char* ORT_API_CALL provider(const OrtCustomOp*){return "CPUExecutionProvider";}
size_t ORT_API_CALL inputs(const OrtCustomOp*){return 3;}size_t ORT_API_CALL outputs(const OrtCustomOp*){return 2;}
ONNXTensorElementDataType ORT_API_CALL itype(const OrtCustomOp*,size_t i){return i==0?ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT:i==1?ONNX_TENSOR_ELEMENT_DATA_TYPE_BOOL:ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64;}
ONNXTensorElementDataType ORT_API_CALL otype(const OrtCustomOp*,size_t){return ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT;}
OrtCustomOpInputOutputCharacteristic ORT_API_CALL characteristic(const OrtCustomOp*,size_t){return INPUT_OUTPUT_REQUIRED;}
OrtMemType ORT_API_CALL mem(const OrtCustomOp*,size_t){return OrtMemTypeDefault;}
}
extern "C" ORT_EXPORT OrtStatus* ORT_API_CALL RegisterCustomOps(OrtSessionOptions* options,const OrtApiBase* base){
 api=base->GetApi(ORT_API_VERSION);if(!api)return nullptr;
 op={};op.version=ORT_API_VERSION;op.GetName=name;op.GetExecutionProviderType=provider;
 op.GetInputTypeCount=inputs;op.GetOutputTypeCount=outputs;op.GetInputType=itype;op.GetOutputType=otype;
 op.GetInputCharacteristic=characteristic;op.GetOutputCharacteristic=characteristic;op.GetInputMemoryType=mem;
 op.CreateKernelV2=create;op.KernelComputeV2=compute;op.KernelDestroy=destroy;
 OrtCustomOpDomain* domain=nullptr;auto s=api->CreateCustomOpDomain("prototype.cpu.se_pool",&domain);if(s)return s;
 s=api->CustomOpDomain_Add(domain,&op);if(!s)s=api->AddCustomOpDomain(options,domain);
 if(s){api->ReleaseCustomOpDomain(domain);return s;}domains.push_back(domain);return nullptr;
}
extern "C" uint64_t se_pool_calls(){return calls.load(std::memory_order_relaxed);}
