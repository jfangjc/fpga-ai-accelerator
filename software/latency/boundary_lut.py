"""Collapse exact scalar DQ/Relu/Where/Q chains using exhaustively checked LUTs.

Use identically on CPU and FPGA graphs so CPU benefits remain in the baseline.
Tables are evaluated by ORT itself for every possible input byte and mask bit.
"""
import copy
import numpy as np
import onnx
from onnx import helper, numpy_helper, TensorProto
import onnxruntime as ort


def transform(model):
    graph=copy.deepcopy(model)
    producers={o:n for n in graph.graph.node for o in n.output}
    arrays={v.name:numpy_helper.to_array(v) for v in graph.graph.initializer}
    records=[];replacements={};added=[]
    outputs={v.name for v in graph.graph.output}
    for q in graph.graph.node:
        if q.op_type!='QuantizeLinear' or any(name not in arrays or arrays[name].size!=1 for name in q.input[1:3]):
            continue
        chain=[q];cursor=q.input[0];mask=None
        while cursor in producers:
            n=producers[cursor]
            if n.op_type=='Relu':
                chain.append(n);cursor=n.input[0]
            elif n.op_type=='Where' and mask is None:
                zero=arrays.get(n.input[1])
                if zero is None and n.input[1] in producers and producers[n.input[1]].op_type=='Constant':
                    cn=producers[n.input[1]]
                    zero=numpy_helper.to_array(helper.get_attribute_value(next(a for a in cn.attribute if a.name=='value')))
                if zero is None or zero.size!=1 or zero.dtype!=np.float32 or np.any(zero!=0):break
                chain.append(n);mask=n.input[0];cursor=n.input[2]
            else:break
        dq=producers.get(cursor)
        if dq is None or dq.op_type!='DequantizeLinear' or any(name not in arrays or arrays[name].size!=1 for name in dq.input[1:3]):continue
        if arrays[dq.input[2]].dtype!=np.uint8 or arrays[q.input[2]].dtype!=np.uint8:continue
        chain.append(dq)
        # Tiny isolated original subgraph: every byte, both mask values.
        tiny_nodes=list(reversed(copy.deepcopy(chain)))
        const=[];used={i for n in tiny_nodes for i in n.input}
        for v in graph.graph.initializer:
            if v.name in used:const.append(copy.deepcopy(v))
        for n in tiny_nodes:
            if n.op_type=='Where' and n.input[1] not in arrays:
                const.append(numpy_helper.from_array(np.array(0,np.float32),n.input[1]))
        inputs=[helper.make_tensor_value_info(dq.input[0],TensorProto.UINT8,[1,1,256])]
        if mask:inputs.append(helper.make_tensor_value_info(mask,TensorProto.BOOL,[1,1,256]))
        tiny=helper.make_model(helper.make_graph(tiny_nodes,'boundary-table',inputs,
                        [helper.make_tensor_value_info(q.output[0],TensorProto.UINT8,[1,1,256])],const),
                        opset_imports=[copy.deepcopy(v) for v in graph.opset_import if not v.domain])
        tiny.ir_version=graph.ir_version
        opt=ort.SessionOptions();opt.intra_op_num_threads=1;opt.inter_op_num_threads=1
        runner=ort.InferenceSession(tiny.SerializeToString(),opt,providers=['CPUExecutionProvider'])
        tables=[]
        for bit in (False,True):
            feed={dq.input[0]:np.arange(256,dtype=np.uint8).reshape(1,1,256)}
            if mask:feed[mask]=np.full((1,1,256),bit,np.bool_)
            tables.append(runner.run(None,feed)[0].reshape(256))
        name=q.name+'_lut'
        lut_name=name+'_table';added.append(numpy_helper.from_array(np.stack(tables),lut_name))
        if mask is None:
            mask=name+'_nomask';added.append(numpy_helper.from_array(np.zeros((1,1,1),np.bool_),mask))
        replacements[q.name]=helper.make_node('LookupMask',[dq.input[0],mask,lut_name],list(q.output),
                                               name=name,domain='prototype.cpu.boundary')
        records.append({'quantizer':q.name,'source':dq.input[0],'nodes':[n.name for n in chain],
                        'mask':mask,'all_512_table_cases_evaluated':True})
    nodes=[replacements.get(n.name,n) for n in graph.graph.node]
    # Retain shared branches; discard only pure nodes whose outputs became dead.
    live=set(outputs);kept=[]
    for n in reversed(nodes):
        if any(o in live for o in n.output):
            kept.append(n);live.update(n.input)
    del graph.graph.node[:];graph.graph.node.extend(reversed(kept))
    graph.graph.initializer.extend(added)
    if records:graph.opset_import.append(helper.make_opsetid('prototype.cpu.boundary',1))
    onnx.checker.check_model(graph)
    return graph,records
