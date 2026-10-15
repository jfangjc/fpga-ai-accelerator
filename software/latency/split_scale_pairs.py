"""Retain two scale-changing pairs using exact identity stages and existing LUTs.

No arithmetic approximation or protocol extension. The extra identity stages
cost compute/weight traffic; this is an experimental compiler, not a speed claim.
"""
import copy
import numpy as np
from onnx import helper,numpy_helper

TARGETS={
    '/encoder/encoder.3/mconv.6/conv/Conv_quant',
    '/encoder/encoder.16/mconv.16/conv/Conv_quant',
}

def expand(model):
    graph=copy.deepcopy(model)
    producers={o:n for n in graph.graph.node for o in n.output}
    nodes=list(graph.graph.node);insert_before={};records=[]
    for pw in nodes:
        if pw.name not in TARGETS:continue
        q=producers[pw.input[0]];where=producers[q.input[0]]
        dq=producers[where.input[2]];dw=producers[dq.input[0]]
        assert (q.op_type,where.op_type,dq.op_type,dw.op_type)==('QuantizeLinear','Where','DequantizeLinear','QLinearConv')
        stem=pw.name+'_scale_split'
        def init(suffix,value):
            name=stem+'_'+suffix
            graph.graph.initializer.append(numpy_helper.from_array(value,name))
            return name
        ones=init('ones',np.ones(256,np.float32))
        zero_bias=init('zero_bias',np.zeros(256,np.int32))
        zero_weight=init('zero_weight',np.array(0,np.int8))
        identity_pw=init('identity_pw',np.eye(256,dtype=np.int8).reshape(256,256,1))
        identity_dw=init('identity_dw',np.ones((256,1,1),np.int8))
        def boundary(prefix,input_name,scale,zero):
            ns=[helper.make_node('DequantizeLinear',[input_name,scale,zero],[prefix+'_float'],name=prefix+'_dq'),
                helper.make_node('Where',[where.input[0],where.input[1],prefix+'_float'],[prefix+'_masked'],name=prefix+'_where'),
                helper.make_node('QuantizeLinear',[prefix+'_masked',scale,zero],[prefix+'_uint8'],name=prefix+'_q')]
            return ns,prefix+'_uint8'
        front,front_q=boundary(stem+'_front',dw.output[0],dw.input[6],dw.input[7])
        front.append(helper.make_node('QLinearConv',
            [front_q,dw.input[6],dw.input[7],identity_pw,ones,zero_weight,dw.input[6],dw.input[7],zero_bias],
            [stem+'_front_output'],name=stem+'_identity_pw',group=1,kernel_shape=[1],pads=[0,0],strides=[1],dilations=[1]))
        insert_before[dq.name]=front
        dq.input[0]=stem+'_front_output'
        back=[helper.make_node('QLinearConv',
            [q.output[0],pw.input[1],pw.input[2],identity_dw,ones,zero_weight,pw.input[1],pw.input[2],zero_bias],
            [stem+'_back_output'],name=stem+'_identity_dw',group=256,kernel_shape=[1],pads=[0,0],strides=[1],dilations=[1])]
        back_boundary,back_q=boundary(stem+'_back',stem+'_back_output',pw.input[1],pw.input[2])
        back.extend(back_boundary);insert_before[pw.name]=back;pw.input[0]=back_q
        records.append({'dw':dw.name,'pw':pw.name,'identity_stages_added':2})
    assert len(records)==2
    replacement=[]
    for node in nodes:
        replacement.extend(insert_before.get(node.name,[]));replacement.append(node)
    del graph.graph.node[:];graph.graph.node.extend(replacement)
    return graph,records
