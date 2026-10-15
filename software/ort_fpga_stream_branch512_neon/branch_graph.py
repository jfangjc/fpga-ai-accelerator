"""Compile shared-input residual projections into two-output block programs.

Stage zero is an identity DW plus residual PW. Its output is returned separately;
subsequent stages consume the original block input, retaining main-path semantics.
See README.md for the selected physical runtime's results and limits.
"""
import copy,struct
import numpy as np
import onnx
from onnx import helper,numpy_helper
from stream_graph import compile_chains

def compile_branches(model,oracle=False):
    graph,groups=compile_chains(model,oracle)
    arrays={v.name:numpy_helper.to_array(v) for v in graph.graph.initializer}
    nodes=list(graph.graph.node)
    chains=[n for n in nodes if n.op_type in ('FpgaChain','OracleChain')]
    replacements={};removed=set();records=[];bounds=[]
    for node in nodes:
        if node.op_type!='QLinearConv' or '/res.' not in node.name:continue
        prefix=node.name.split('/res.')[0]
        matching=[c for c in chains if c.name.startswith(prefix+'/') and c.input[0]==node.input[0]]
        assert len(matching)==1,(node.name,len(matching))
        chain=matching[0];assert chain.name not in replacements
        attrs={a.name:helper.get_attribute_value(a) for a in node.attribute}
        assert attrs.get('group',1)==1 and attrs.get('pads',[0,0])==[0,0]
        assert attrs.get('dilations',[1])==[1] and attrs.get('strides',[1]) in ([1],[2])
        stride=attrs.get('strides',[1])[0]
        w=arrays[node.input[3]].copy();assert w.shape==(256,256,1) and w.dtype==np.int8
        assert np.all(arrays[node.input[5]]==0)
        sx=arrays[node.input[1]];sy=arrays[node.input[6]];sw=arrays[node.input[4]]
        zx=int(arrays[node.input[2]]);zy=int(arrays[node.input[7]])
        assert sx.size==sy.size==1 and sw.shape==(256,)
        b=arrays[node.input[8]].copy();assert b.shape==(256,) and b.dtype==np.int32
        ratio=(sx*sw)/sy;dead=ratio<=np.float32(2**-32);w[dead]=0;b[dead]=0
        for c in range(256):
            row=w[c].astype(np.int64).ravel()
            lo=int(b[c])+int(np.minimum(row*(-zx),row*(255-zx)).sum())
            hi=int(b[c])+int(np.maximum(row*(-zx),row*(255-zx)).sum())
            correction=int(b[c])+(128-zx)*int(row.sum())
            raw_max=128*int(np.abs(row).sum())
            assert raw_max<(1<<31)
            wrapped_correction=(correction+(1<<31))%(1<<32)-(1<<31)
            bounds.append({'name':node.name,'channel':c,'minimum':lo,'maximum':hi,
                'biased_correction':wrapped_correction,'unbiased_accumulator_absolute_bound':raw_max,
                'int32_bias_wrap_required':lo<-(1<<31) or hi>=(1<<31)})
        body=arrays[chain.input[2]].tobytes();magic,count=struct.unpack_from('<II',body)
        assert magic==0x314e4843 and 1<=count<8
        stage=bytearray(struct.pack('<IIIIfff',1,zx,zx,zy,float(sx),float(sx),float(sy)))
        for arr,dtype in ((np.ones(256,np.float32),'<f4'),(sw,'<f4'),(np.zeros(256,np.int32),'<i4'),
                          (b,'<i4'),(np.ones((256,1,1),np.int8),'i1'),(w,'i1')):
            stage.extend(np.ascontiguousarray(arr,dtype=dtype).tobytes())
        stage.extend(bytes(range(256)));stage.extend(struct.pack('<I',zx))
        payload=struct.pack('<II',magic,count+1)+stage+body[8:]
        program=chain.name+'_branch_program'
        graph.graph.initializer.append(numpy_helper.from_array(np.frombuffer(payload,np.uint8).copy(),program))
        replacement=helper.make_node('OracleBranch' if oracle else 'FpgaBranch',
            [chain.input[0],chain.input[1],program],[chain.output[0],node.output[0]],
            name=chain.name+'_residual_branch',domain='prototype.fpga.stream.branch512',residual_stride=stride)
        replacements[chain.name]=replacement;removed.add(node.name)
        records.append({'residual':node.name,'chain':chain.name,'shared_input':chain.input[0],
                        'main_pairs':count,'total_pairs':count+1,'residual_stride':stride,'constant_output_rows':int(dead.sum())})
    assert len(records)==21
    newnodes=[]
    for node in nodes:
        if node.name in removed:continue
        node=replacements.get(node.name,node)
        if node.domain=='prototype.fpga.stream.registered':node.domain='prototype.fpga.stream.branch512'
        newnodes.append(node)
    # Original residual consumers can precede the main-chain replacement.
    # Schedule them after the new two-output producer without changing math.
    known={v.name for v in graph.graph.input}|{v.name for v in graph.graph.initializer}|{''}
    pending=newnodes;ordered=[]
    while pending:
        deferred=[]
        for node in pending:
            if all(i in known for i in node.input):
                ordered.append(node);known.update(node.output)
            else:deferred.append(node)
        assert len(deferred)<len(pending),'Unresolved or cyclic branch graph'
        pending=deferred
    del graph.graph.node[:];graph.graph.node.extend(ordered)
    retained=[o for o in graph.opset_import if o.domain!='prototype.fpga.stream.registered']
    del graph.opset_import[:];graph.opset_import.extend(retained)
    graph.opset_import.append(helper.make_opsetid('prototype.fpga.stream.branch512',1))
    onnx.checker.check_model(graph)
    return graph,records,bounds
