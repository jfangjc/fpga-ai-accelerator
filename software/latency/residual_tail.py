"""Fuse SE multiplication/residual DQ/add/activation/mask/quantization exactly.

Apply to both CPU and FPGA graphs. SE reductions and gates stay in ORT.
"""
import copy
import numpy as np
import onnx
from onnx import helper,numpy_helper

def transform(model):
    graph=copy.deepcopy(model);nodes=list(graph.graph.node)
    producers={o:n for n in nodes for o in n.output}
    arrays={v.name:numpy_helper.to_array(v) for v in graph.graph.initializer}
    constants={n.output[0]:numpy_helper.to_array(helper.get_attribute_value(next(a for a in n.attribute if a.name=='value')))
               for n in nodes if n.op_type=='Constant' and any(a.name=='value' for a in n.attribute)}
    arrays.update(constants);replacements={};records=[];added=[]
    for q in nodes:
        if q.op_type!='QuantizeLinear':continue
        if any(i not in arrays or arrays[i].size!=1 for i in q.input[1:]):continue
        if arrays[q.input[1]].dtype!=np.float32 or arrays[q.input[2]].dtype!=np.uint8:continue
        cursor=q.input[0];tail=[q];mask=None;relu=False
        while cursor in producers:
            n=producers[cursor]
            if n.op_type=='Relu' and not relu:relu=True;cursor=n.input[0];tail.append(n)
            elif n.op_type=='Where' and mask is None:
                zero=arrays.get(n.input[1])
                if zero is None or zero.size!=1 or zero.dtype!=np.float32 or zero.item()!=0:break
                mask=n.input[0];cursor=n.input[2];tail.append(n)
            else:break
        add=producers.get(cursor)
        if add is None or add.op_type!='Add':continue
        branches=[producers.get(i) for i in add.input]
        if any(n is None for n in branches):continue
        if [n.op_type for n in branches]==['Mul','DequantizeLinear']:mul,dq=branches
        elif [n.op_type for n in branches]==['DequantizeLinear','Mul']:dq,mul=branches
        else:continue
        if any(i not in arrays or arrays[i].size!=1 for i in dq.input[1:]):continue
        if arrays[dq.input[1]].dtype!=np.float32 or arrays[dq.input[2]].dtype!=np.uint8:continue
        # Restrict to channel gate broadcast produced by this checkpoint's SE.
        gate=producers.get(mul.input[1])
        if gate is None or gate.op_type!='Sigmoid':continue
        if mask is None:
            mask=q.name+'_nomask';added.append(numpy_helper.from_array(np.zeros((1,1,1),np.bool_),mask))
        inputs=[*mul.input,dq.input[0],mask,*dq.input[1:],*q.input[1:]]
        replacements[q.name]=helper.make_node('ResidualTail',inputs,list(q.output),name=q.name+'_fused_tail',
                                             domain='prototype.cpu.residual',relu=int(relu))
        records.append({'quantizer':q.name,'inputs':inputs,'nodes':[n.name for n in tail+[add,mul,dq]],'relu':relu})
    newnodes=[replacements.get(n.name,n) for n in nodes]
    live={v.name for v in graph.graph.output};kept=[]
    for n in reversed(newnodes):
        if any(o in live for o in n.output):kept.append(n);live.update(n.input)
    del graph.graph.node[:];graph.graph.node.extend(reversed(kept));graph.graph.initializer.extend(added)
    if records:graph.opset_import.append(helper.make_opsetid('prototype.cpu.residual',1))
    onnx.checker.check_model(graph)
    return graph,records
