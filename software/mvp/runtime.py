"""Build and run the selected retained-block Citrinet graph."""
import ctypes
from pathlib import Path
import sys
import time

import numpy as np
import onnx
import onnxruntime as ort

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'software/latency'))
sys.path.insert(0, str(ROOT/'software/ort_fpga_stream_branch512_neon'))
from pair_graph import candidate
from branch_graph import compile_branches
from split_scale_pairs import expand
from retain_stride_pointwise import retain
from boundary_lut import transform as boundaries
from residual_tail import transform as tails
from se_pool import transform as pooling
from frontend import ValidFrameFrontend, decode


class Stats(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ('ioctls','loads','runs','tx_bytes','rx_bytes')] + [('ioctl_seconds',ctypes.c_double)]


def stats(library):
    state = Stats()
    physical, oracle = ctypes.c_uint64(), ctypes.c_uint64()
    library.fpga_ort_stats(ctypes.byref(state), ctypes.byref(physical), ctypes.byref(oracle))
    return {key:getattr(state,key) for key,_ in state._fields_} | {'physical_calls':physical.value, 'oracle_calls':oracle.value}


def graphs():
    model = onnx.load(ROOT/'models/citrinet-256/cpu-int8-cse.onnx')
    cpu = pooling(tails(boundaries(model)[0])[0])[0]
    expanded = expand(onnx.load(ROOT/'models/citrinet-256/cpu-int8-cse.onnx'))[0]
    graph = candidate(expanded, False)[0]
    graph = retain(boundaries(graph)[0], False)[0]
    physical, groups, _ = compile_branches(graph)
    physical = pooling(tails(physical)[0])[0]
    count = sum(n.domain == 'prototype.fpga.stream.branch512' for n in physical.graph.node)
    if len(groups) != 21 or count != 24:
        raise RuntimeError('Compiled graph differs from the selected 24-transaction graph')
    return cpu, physical


def session(graph, policy, physical=False):
    options = ort.SessionOptions()
    options.intra_op_num_threads = policy['threads']
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = (ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED
        if policy['level'] == 'extended' else ort.GraphOptimizationLevel.ORT_ENABLE_ALL)
    options.log_severity_level = 3
    for key, value in (('session.intra_op.allow_spinning','1' if policy['spin'] else '0'),
                       ('session.inter_op.allow_spinning','0'),
                       ('session.intra_op.spin_duration_us',str(policy['spin'])),
                       ('session.intra_op.spin_backoff_max','8')):
        options.add_session_config_entry(key, value)
    if policy['threads'] > 1:
        options.add_session_config_entry('session.intra_op_thread_affinities',
            ';'.join(str(i+1) for i in range(policy['threads']-1)))
    libraries = [ROOT/'software/latency/libboundary_lut.so']
    if any(n.domain == 'prototype.cpu.se_pool' for n in graph.graph.node):
        libraries.append(ROOT/'software/latency/libse_pool.so')
    if any(n.domain == 'prototype.cpu.residual' for n in graph.graph.node):
        suffix = '' if policy.get('tail_impl','maskfast') == 'original' else '_maskfast'
        libraries.append(ROOT/f'software/latency/libresidual_tail{suffix}.so')
    if physical:
        libraries.append(ROOT/'software/ort_fpga_stream_branch512_neon/libort_fpga.so')
    for path in libraries:
        options.register_custom_ops_library(str(path))
    return ort.InferenceSession(onnx.shape_inference.infer_shapes(graph).SerializeToString(),options,
                               providers=['CPUExecutionProvider'])


class AudioTimer:
    def __init__(self):
        self.library = ctypes.CDLL(str(ROOT/'software/ort_fpga_stream_branch512_neon/libort_fpga.so'))
        self.frontend = ValidFrameFrontend(ROOT/'models/citrinet-256')
        self.vocabulary = (ROOT/'models/citrinet-256/vocab.txt').read_text().splitlines()

    def call(self, runner, audio, physical=True, cold=False):
        before = stats(self.library)
        start = time.perf_counter()
        features, length = self.frontend(audio)
        frontend_end = time.perf_counter()
        logits, encoded = runner.run(None, {'features':features, 'feature_lengths':length})
        model_end = time.perf_counter()
        text = decode(logits[0], encoded[0], self.vocabulary)
        end = time.perf_counter()
        delta = {key:value-before[key] for key,value in stats(self.library).items()}
        expected = {'physical_calls':128, 'oracle_calls':0, 'loads':256, 'ioctls':48 if cold else 24} if physical else {'physical_calls':0,'oracle_calls':0,'ioctls':0}
        if any(delta[key] != value for key,value in expected.items()):
            raise RuntimeError('Unexpected physical work: '+str(delta))
        return {'seconds':end-start,'frontend_seconds':frontend_end-start,
                'model_seconds':model_end-frontend_end,'decode_seconds':end-model_end,
                'text':text,'length':int(encoded[0]),'feature_frames':int(length[0]),
                'counters':delta}, logits
