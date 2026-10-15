// Exact UINT8 lookup for DQ/activation/mask/Q boundaries. No FPGA accesses.
#include "../ort_fpga_shortwide/include/onnxruntime_c_api.h"
#include <cstdint>
#include <cstring>
#include <algorithm>
#include <stdexcept>
#include <string>
#include <vector>
#include <cmath>
#if defined(__aarch64__) && defined(__ARM_NEON)
#include <arm_neon.h>
#endif
namespace {
const OrtApi* api;
void check(OrtStatus* status) {
    if (status) { std::string text=api->GetErrorMessage(status); api->ReleaseStatus(status); throw std::runtime_error(text); }
}
std::vector<int64_t> shape(const OrtValue* value) {
    OrtTensorTypeAndShapeInfo* info=nullptr;
    check(api->GetTensorTypeAndShape(value,&info));
    size_t count=0;
    OrtStatus* status=api->GetDimensionsCount(info,&count);
    if(status) {api->ReleaseTensorTypeAndShapeInfo(info);check(status);}
    std::vector<int64_t> dims(count);
    status=api->GetDimensions(info,dims.data(),count);
    api->ReleaseTensorTypeAndShapeInfo(info);check(status);return dims;
}
template<class T> const T* data(const OrtValue* value) {
    const void* pointer=nullptr;check(api->GetTensorData(value,&pointer));return static_cast<const T*>(pointer);
}
struct Kernel {
    float residual_scale,output_scale;uint8_t residual_zero,output_zero;bool relu;
    explicit Kernel(const OrtKernelInfo* info){
        int64_t flag=0;check(api->KernelInfoGetAttribute_int64(info,"relu",&flag));relu=flag!=0;
        for(size_t i=4;i<8;i++){
            int constant=0;const OrtValue* value=nullptr;
            check(api->KernelInfoGetConstantInput_tensor(info,i,&constant,&value));
            if(!constant || !shape(value).empty())throw std::runtime_error("Residual scales/zeros must be scalar constants");
            if(i==4)residual_scale=*data<float>(value);
            if(i==5)residual_zero=*data<uint8_t>(value);
            if(i==6)output_scale=*data<float>(value);
            if(i==7)output_zero=*data<uint8_t>(value);
        }
        if(!(residual_scale>0 && output_scale>0))throw std::runtime_error("Invalid residual scales");
    }
    void compute(OrtKernelContext* context){
        const OrtValue* values[4];for(size_t i=0;i<4;i++)check(api->KernelContext_GetInput(context,i,&values[i]));
        auto dims=shape(values[0]);
        if(dims.size()!=3 || dims[0]!=1 || dims[1]<=0 || dims[2]<=0 || shape(values[2])!=dims ||
            shape(values[1])!=std::vector<int64_t>{1,dims[1],1})throw std::runtime_error("Invalid residual broadcast");
        auto md=shape(values[3]);if(md!=std::vector<int64_t>{1,1,1} && md!=std::vector<int64_t>{1,1,dims[2]})
            throw std::runtime_error("Invalid residual mask");
        auto x=data<float>(values[0]),gate=data<float>(values[1]);auto res=data<uint8_t>(values[2]);auto mask=data<bool>(values[3]);
        OrtValue* output=nullptr;check(api->KernelContext_GetOutput(context,0,dims.data(),dims.size(),&output));
        void* pointer=nullptr;check(api->GetTensorMutableData(output,&pointer));auto y=static_cast<uint8_t*>(pointer);
        size_t times=dims[2];
        for(size_t c=0;c<static_cast<size_t>(dims[1]);c++){
            size_t offset=c*times,t=0;
#if defined(__aarch64__) && defined(__ARM_NEON)
            auto scale=vdupq_n_f32(residual_scale),outscale=vdupq_n_f32(output_scale),g=vdupq_n_f32(gate[c]);
            auto minimum=vdupq_n_f32(-static_cast<float>(output_zero)),maximum=vdupq_n_f32(255.f-output_zero);
            auto zero=vdupq_n_f32(0);auto z=vdupq_n_s32(output_zero);
            for(;t+4<=times;t+=4){
                uint32_t packed;std::memcpy(&packed,res+offset+t,4);
                auto bytes=vreinterpret_u8_u32(vdup_n_u32(packed));
                auto integers=vreinterpretq_s32_u32(vmovl_u16(vget_low_u16(vmovl_u8(bytes))));
                auto r=vmulq_f32(vcvtq_f32_s32(vsubq_s32(integers,vdupq_n_s32(residual_zero))),scale);
                // Separate rounded multiply and add, matching original ORT nodes.
                auto result=vaddq_f32(vmulq_f32(vld1q_f32(x+offset+t),g),r);
                if(relu)result=vmaxq_f32(result,zero);
                uint32_t m[4];for(size_t i=0;i<4;i++)m[i]=mask[md[2]==1?0:t+i]?0xffffffffu:0;
                result=vbslq_f32(vld1q_u32(m),zero,result);
                result=vminnmq_f32(vmaxnmq_f32(vdivq_f32(result,outscale),minimum),maximum);
                auto q=vaddq_s32(vcvtnq_s32_f32(result),z);
                auto words=vqmovun_s32(q);auto out=vqmovn_u16(vcombine_u16(words,words));
                uint32_t final=vget_lane_u32(vreinterpret_u32_u8(out),0);std::memcpy(y+offset+t,&final,4);
            }
#endif
            for(;t<times;t++){
                float r=(static_cast<int>(res[offset+t])-residual_zero)*residual_scale;
                float value=x[offset+t]*gate[c];value=value+r;
                if(relu)value=std::max(value,0.f);
                if(mask[md[2]==1?0:t])value=0;
                value=std::fmin(std::fmax(value/output_scale,-static_cast<float>(output_zero)),255.f-output_zero);
                y[offset+t]=static_cast<uint8_t>(static_cast<int>(std::nearbyint(value))+output_zero);
            }
        }
    }
};
OrtCustomOp op={};
OrtStatus* ORT_API_CALL create(const OrtCustomOp*,const OrtApi* a,const OrtKernelInfo* info,void** result) {
    api=a;try{*result=new Kernel(info);return nullptr;}catch(const std::exception& e){return api->CreateStatus(ORT_FAIL,e.what());}
}
OrtStatus* ORT_API_CALL compute(void* kernel,OrtKernelContext* context) {
    try{static_cast<Kernel*>(kernel)->compute(context);return nullptr;}catch(const std::exception& e){return api->CreateStatus(ORT_FAIL,e.what());}
}
void ORT_API_CALL destroy(void* kernel){delete static_cast<Kernel*>(kernel);}
const char* ORT_API_CALL name(const OrtCustomOp*){return "ResidualTail";}
const char* ORT_API_CALL provider(const OrtCustomOp*){return "CPUExecutionProvider";}
size_t ORT_API_CALL inputs(const OrtCustomOp*){return 8;}
size_t ORT_API_CALL outputs(const OrtCustomOp*){return 1;}
ONNXTensorElementDataType ORT_API_CALL input_type(const OrtCustomOp*,size_t i){return i==3?ONNX_TENSOR_ELEMENT_DATA_TYPE_BOOL:(i==0 || i==1 || i==4 || i==6)?ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT:ONNX_TENSOR_ELEMENT_DATA_TYPE_UINT8;}
ONNXTensorElementDataType ORT_API_CALL output_type(const OrtCustomOp*,size_t){return ONNX_TENSOR_ELEMENT_DATA_TYPE_UINT8;}
OrtCustomOpInputOutputCharacteristic ORT_API_CALL characteristic(const OrtCustomOp*,size_t){return INPUT_OUTPUT_REQUIRED;}
OrtMemType ORT_API_CALL memory(const OrtCustomOp*,size_t){return OrtMemTypeDefault;}
std::vector<OrtCustomOpDomain*> domains;
}
extern "C" ORT_EXPORT OrtStatus* ORT_API_CALL RegisterCustomOps(OrtSessionOptions* options,const OrtApiBase* base){
    api=base->GetApi(ORT_API_VERSION);if(!api)return nullptr;
    op.version=ORT_API_VERSION;op.GetName=name;op.GetExecutionProviderType=provider;
    op.GetInputTypeCount=inputs;op.GetOutputTypeCount=outputs;op.GetInputType=input_type;op.GetOutputType=output_type;
    op.GetInputCharacteristic=characteristic;op.GetOutputCharacteristic=characteristic;op.GetInputMemoryType=memory;
    op.CreateKernelV2=create;op.KernelComputeV2=compute;op.KernelDestroy=destroy;
    OrtCustomOpDomain* domain=nullptr;OrtStatus* status=api->CreateCustomOpDomain("prototype.cpu.residual",&domain);
    if(status)return status;
    status=api->CustomOpDomain_Add(domain,&op);if(!status)status=api->AddCustomOpDomain(options,domain);
    if(status){api->ReleaseCustomOpDomain(domain);return status;}
    domains.push_back(domain);return nullptr;
}
