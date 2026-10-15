"""Compile consecutive physical pairs and exact LookupMask boundaries to chains."""
import copy
import struct
import numpy as np
import onnx
from onnx import helper,numpy_helper


def compile_chains(model,oracle=False):
    graph=copy.deepcopy(model)
    arrays={v.name:numpy_helper.to_array(v) for v in graph.graph.initializer}
    nodes=list(graph.graph.node);producers={o:n for n in nodes for o in n.output}
    consumers={}
    for n in nodes:
        for i in n.input:consumers.setdefault(i,[]).append(n)
    outputs={v.name for v in graph.graph.output}
    pairs=[n for n in nodes if n.op_type in ('FpgaDepthwisePointwise','OracleDepthwisePointwise')]
    links={};previous={}
    for pair in pairs:
        bridge=producers.get(pair.input[0])
        if bridge is None or bridge.op_type!='LookupMask' or bridge.domain!='prototype.cpu.boundary':continue
        parent=producers.get(bridge.input[0])
        if parent is None or parent not in pairs or parent.input[15]!=pair.input[15] or bridge.input[1]!=pair.input[15]:continue
        if len(consumers[parent.output[0]])!=1 or len(consumers[bridge.output[0]])!=1:continue
        if parent.output[0] in outputs or bridge.output[0] in outputs:continue
        lut=arrays[bridge.input[2]]
        assert lut.shape==(2,256) and lut.dtype==np.uint8 and np.all(lut[1]==lut[1,0])
        links[parent.name]=(pair,bridge,lut);previous[pair.name]=parent.name
    groups=[];removed=set();replacements={}
    for pair in pairs:
        if pair.name in previous:continue
        chain=[pair];bridges=[];luts=[]
        while chain[-1].name in links:
            following,bridge,lut=links[chain[-1].name]
            chain.append(following);bridges.append(bridge);luts.append(lut)
        assert len(chain)<=8
        payload=bytearray(struct.pack('<II',0x314e4843,len(chain)))
        for i,n in enumerate(chain):
            dw=arrays[n.input[3]];pw=arrays[n.input[9]]
            assert dw.shape[0:2]==(256,1) and pw.shape==(256,256,1)
            assert np.all(arrays[n.input[5]]==0) and np.all(arrays[n.input[11]]==0)
            # ARM ORT converts its wrapped INT32 sum to float32. If the
            # output scale ratio is <= 2^-32, every possible INT32 value
            # rounds to zero (including +/-0.5 ties). Its UINT8 result is
            # always the output zero point. Remove those constant rows;
            # this also avoids meaningless overflow in saturated biases.
            dw=dw.copy();pw=pw.copy()
            db=arrays[n.input[8]].copy();pb=arrays[n.input[14]].copy()
            dr=(arrays[n.input[1]]*arrays[n.input[4]])/arrays[n.input[6]]
            pr=(arrays[n.input[6]]*arrays[n.input[10]])/arrays[n.input[12]]
            dz=dr<=np.float32(2**-32);pz=pr<=np.float32(2**-32)
            dw[dz]=0;db[dz]=0;pw[pz]=0;pb[pz]=0
            compiled_arrays={3:dw,9:pw,8:db,14:pb}
            payload.extend(struct.pack('<IIIIfff',dw.shape[2],int(arrays[n.input[2]]),
                        int(arrays[n.input[7]]),int(arrays[n.input[13]]),
                        float(arrays[n.input[1]]),float(arrays[n.input[6]]),float(arrays[n.input[12]])))
            for index,dtype in ((4,'<f4'),(10,'<f4'),(8,'<i4'),(14,'<i4'),(3,'i1'),(9,'i1')):
                payload.extend(np.ascontiguousarray(compiled_arrays.get(index,arrays[n.input[index]]),dtype=dtype).tobytes())
            if i<len(luts):payload.extend(luts[i][0].tobytes());masked=int(luts[i][1,0])
            else:payload.extend(bytes(range(256)));masked=0
            payload.extend(struct.pack('<I',masked))
        name=chain[-1].name+'_chain';program=name+'_program'
        graph.graph.initializer.append(numpy_helper.from_array(np.frombuffer(payload,dtype=np.uint8).copy(),program))
        replacements[chain[-1].name]=helper.make_node('OracleChain' if oracle else 'FpgaChain',
                    [chain[0].input[0],chain[0].input[15],program],list(chain[-1].output),
                    name=name,domain='prototype.fpga.stream.registered')
        removed.update(n.name for n in chain+bridges)
        groups.append({'name':name,'pairs':[n.name for n in chain],'retained_boundaries':len(bridges)})
    newnodes=[]
    for n in nodes:
        if n.name in replacements:newnodes.append(replacements[n.name])
        elif n.name not in removed:newnodes.append(n)
    del graph.graph.node[:];graph.graph.node.extend(newnodes)
    graph.opset_import.append(helper.make_opsetid('prototype.fpga.stream.registered',1))
    onnx.checker.check_model(graph)
    assert sum(len(g['pairs']) for g in groups)==len(pairs)
    return graph,groups
