"""Offload three PW tails while their stride-two DW remains on CPU.

Use the unchanged retained-pair protocol with an exact identity DW. This adds
three calls and must beat CPU including that overhead before adoption.
"""
import copy
import numpy as np
from onnx import helper,numpy_helper
TARGETS={f'/encoder/encoder.{i}/mconv.21/conv/Conv_quant' for i in (1,7,14)}
def retain(model,oracle=False):
    graph=copy.deepcopy(model)
    producers={o:n for n in graph.graph.node for o in n.output}
    arrays={v.name:numpy_helper.to_array(v) for v in graph.graph.initializer}
    replacements={};records=[]
    for node in graph.graph.node:
        if node.name not in TARGETS:continue
        assert node.op_type=='QLinearConv'
        attrs={a.name:helper.get_attribute_value(a) for a in node.attribute}
        assert attrs.get('group',1)==1 and attrs.get('strides',[1])==[1] and attrs.get('dilations',[1])==[1]
        assert attrs.get('pads',[0,0])==[0,0] and arrays[node.input[3]].shape==(256,256,1)
        assert np.all(arrays[node.input[5]]==0)
        boundary=producers[node.input[0]]
        assert boundary.op_type=='LookupMask' and boundary.domain=='prototype.cpu.boundary'
        stem=node.name+'_retained_stride_tail'
        def init(suffix,value):
            name=stem+'_'+suffix
            graph.graph.initializer.append(numpy_helper.from_array(value,name))
            return name
        dw=init('identity_dw',np.ones((256,1,1),np.int8))
        ones=init('ones',np.ones(256,np.float32))
        wz=init('wzero',np.array(0,np.int8))
        bias=init('zero_bias',np.zeros(256,np.int32))
        pw_bias=node.input[8] if len(node.input)==9 else bias
        inputs=[node.input[0],node.input[1],node.input[2],dw,ones,wz,node.input[1],node.input[2],bias,
                *list(node.input[3:8]),pw_bias,boundary.input[1]]
        assert len(inputs)==16
        replacements[node.name]=helper.make_node('OracleDepthwisePointwise' if oracle else 'FpgaDepthwisePointwise',
            inputs,list(node.output),name=stem,domain='prototype.fpga.stream.registered.pair')
        records.append({'pointwise':node.name,'depthwise_on_cpu':True,'identity_depthwise_added':True})
    assert len(records)==3
    nodes=[replacements.get(n.name,n) for n in graph.graph.node]
    del graph.graph.node[:];graph.graph.node.extend(nodes)
    return graph,records
