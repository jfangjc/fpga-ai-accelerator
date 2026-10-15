"""Replace an exact DW11/DQ/mask/Q/PW boundary with one retained FPGA op."""
import copy
import numpy as np
import onnx
from onnx import helper,numpy_helper


def one_pair(model, dw_name='/encoder/encoder.1/mconv.5/conv/Conv_quant',
              pw_name='/encoder/encoder.1/mconv.6/conv/Conv_quant', oracle=False):
    g = onnx.shape_inference.infer_shapes(copy.deepcopy(model))
    nodes = {n.name:n for n in g.graph.node}
    producers = {o:n for n in g.graph.node for o in n.output}
    consumers = {}
    for n in g.graph.node:
        for i in n.input: consumers.setdefault(i, []).append(n)
    arrays = {v.name:numpy_helper.to_array(v) for v in g.graph.initializer}
    dw, pw = nodes[dw_name], nodes[pw_name]
    assert dw.op_type == pw.op_type == 'QLinearConv'
    da = {v.name:helper.get_attribute_value(v) for v in dw.attribute}
    pa = {v.name:helper.get_attribute_value(v) for v in pw.attribute}
    wd,wp = arrays[dw.input[3]],arrays[pw.input[3]]
    assert wd.shape[1] == 1 and (1<=wd.shape[2]<=41 and wd.shape[2]%2==1) and wp.shape[1:] == (wd.shape[0],1)
    assert wd.shape[0] == 256 and wp.shape[0] == 256
    assert da.get('group') == wd.shape[0] and da.get('pads') == [wd.shape[2]//2]*2
    assert da.get('strides',[1]) == da.get('dilations',[1]) == [1]
    assert pa.get('group',1) == 1 and pa.get('strides',[1]) == pa.get('dilations',[1]) == [1]
    assert pa.get('pads',[0,0]) == [0,0]
    q = producers[pw.input[0]]; where = producers[q.input[0]]; dq = producers[where.input[2]]
    assert q.op_type == 'QuantizeLinear' and where.op_type == 'Where' and dq.op_type == 'DequantizeLinear'
    assert dq.input[0] == dw.output[0] and dq.input[1:3] == dw.input[6:8]
    assert q.input[1:3] == pw.input[1:3] == dw.input[6:8]
    # Mask's true branch must be FP32 zero, an initializer or Constant.
    zero = arrays.get(where.input[1])
    if zero is None:
        c = producers[where.input[1]]; assert c.op_type == 'Constant'
        zero = numpy_helper.to_array(helper.get_attribute_value(next(v for v in c.attribute if v.name=='value')))
    assert zero.dtype == np.float32 and np.all(zero == 0)
    sequence = [dw,dq,where,q,pw]
    for left,right in zip(sequence,sequence[1:]):
        assert len(consumers[left.output[0]]) == 1 and consumers[left.output[0]][0] is right
    assert not any(v.name in {n.output[0] for n in sequence[:-1]} for v in g.graph.output)
    def bias(n,channels):
        if len(n.input) == 9: return n.input[8]
        name=n.name+'_pair_zero_bias'
        g.graph.initializer.append(numpy_helper.from_array(np.zeros(channels,np.int32),name))
        return name
    inputs = list(dw.input[:8])+[bias(dw,wd.shape[0])]+list(pw.input[3:8])+[bias(pw,wp.shape[0]),where.input[0]]
    fused = helper.make_node('OracleDepthwisePointwise' if oracle else 'FpgaDepthwisePointwise',
                             inputs, list(pw.output), name=pw.name+'_retained_pair', domain='prototype.fpga.stream.registered.pair')
    replacements = []
    for n in g.graph.node:
        if n is pw: replacements.append(fused)
        elif n not in sequence: replacements.append(n)
    del g.graph.node[:];g.graph.node.extend(replacements)
    if not any(v.domain=='prototype.fpga.stream.registered.pair' for v in g.opset_import): g.opset_import.append(helper.make_opsetid('prototype.fpga.stream.registered.pair',1))
    onnx.checker.check_model(g)
    return g


def candidate(model,oracle=True):
    g=copy.deepcopy(model);producers={o:n for n in model.graph.node for o in n.output};selected=[];rejected=[]
    for pw in model.graph.node:
        if pw.op_type!='QLinearConv':continue
        try:
            q=producers[pw.input[0]];where=producers[q.input[0]];dq=producers[where.input[2]];dw=producers[dq.input[0]]
            if dw.op_type!='QLinearConv':continue
            g=one_pair(g,dw.name,pw.name,oracle);selected.append((dw.name,pw.name))
        except (AssertionError,KeyError,IndexError):rejected.append(pw.name)
    assert len(selected)>32, 'No extra pairs selected'
    return g,selected
