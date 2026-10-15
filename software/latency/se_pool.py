"""Fuse masked SE pooling while retaining ORT's floating reduction kernel."""
import copy
import numpy as np
import onnx
from onnx import helper,numpy_helper

def transform(model):
    graph=onnx.shape_inference.infer_shapes(copy.deepcopy(model))
    nodes=list(graph.graph.node);producers={o:n for n in nodes for o in n.output}
    arrays={v.name:numpy_helper.to_array(v) for v in graph.graph.initializer}
    for n in nodes:
        if n.op_type=='Constant' and len(n.attribute)==1 and n.attribute[0].name=='value':
            arrays[n.output[0]]=numpy_helper.to_array(helper.get_attribute_value(n.attribute[0]))
    removed=set();replacement={};records=[]
    for node in nodes:
        if node.op_type!='Div' or '/mconv.23/' not in node.name:continue
        try:
            total=producers[node.input[0]];den=producers[node.input[1]]
            where=producers[total.input[0]];count=producers[den.input[0]];cast=producers[count.input[0]]
            mask=producers[where.input[0]]
            assert total.op_type==count.op_type=='ReduceSum' and where.op_type=='Where'
            assert den.op_type==cast.op_type=='Cast' and mask.op_type=='Cast'
            assert helper.get_attribute_value(den.attribute[0])==onnx.TensorProto.FLOAT
            assert helper.get_attribute_value(cast.attribute[0])==onnx.TensorProto.INT64
            assert helper.get_attribute_value(mask.attribute[0])==onnx.TensorProto.BOOL
            not_node=producers[mask.input[0]];assert not_node.op_type=='Not' and not_node.input[0]==cast.input[0]
            assert len(total.input)==len(count.input)==2 and total.input[1]==count.input[1]
            assert any(np.array_equal(arrays[total.input[1]],np.array([axis],dtype=np.int64)) for axis in (2,-1))
            assert all({a.name:helper.get_attribute_value(a) for a in n.attribute}.get('keepdims',1)==1 for n in (total,count))
            zero=arrays[where.input[1]];assert zero.dtype==np.float32 and np.all(zero==0)
        except (KeyError,AssertionError,IndexError):continue
        replacement[node.name]=helper.make_node('MaskedSEMean',[where.input[2],where.input[0],total.input[1]],
                    [where.output[0],node.output[0]],name=node.name+'_native_pool',domain='prototype.cpu.se_pool')
        removed.update(n.name for n in (where,total,node))
        records.append({'name':node.name,'inputs':[where.input[2],where.input[0],total.input[1]],
                        'outputs':[where.output[0],node.output[0]]})
    assert len(records)==21,('Unexpected SE pooling coverage',len(records))
    result=[replacement[n.name] if n.name in replacement else n for n in nodes if n.name not in removed or n.name in replacement]
    # Remove now-unused count/cast branches only when they have no consumers.
    live={o.name for o in graph.graph.output};kept=[]
    for n in reversed(result):
        if any(o in live for o in n.output):kept.append(n);live.update(n.input)
    del graph.graph.node[:];graph.graph.node.extend(reversed(kept))
    graph.opset_import.append(helper.make_opsetid('prototype.cpu.se_pool',1))
    onnx.checker.check_model(graph)
    return graph,records
