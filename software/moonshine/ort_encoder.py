"""Optional native ARM attention/convolutions alongside FPGA encoder FFNs.

Streaming context and convolution phase remain owned by streaming.py. Convolution
weights stay float32; the optional INT8 policy applies to attention projections.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from .ort_decoder import digest, session_options


class AttentionGraph(torch.nn.Module):
    def __init__(self, layer):
        super().__init__()
        self.layer = layer

    def forward(self, buffer, start, stop, mask):
        layer, a = self.layer, self.layer.self_attn
        norm = layer.input_layernorm(buffer)
        def heads(x, count):
            return x.reshape(1, -1, count, a.head_dim).transpose(1, 2)
        query = heads(a.q_proj(norm[:, start:stop]), a.config.num_attention_heads)
        keys = heads(a.k_proj(norm), a.config.num_key_value_heads)
        values = heads(a.v_proj(norm), a.config.num_key_value_heads)
        if a.num_key_value_groups != 1:
            keys = keys.repeat_interleave(a.num_key_value_groups, dim=1)
            values = values.repeat_interleave(a.num_key_value_groups, dim=1)
        result = F.scaled_dot_product_attention(query, keys, values, attn_mask=mask,
                                                dropout_p=0, scale=a.scaling)
        result = result.transpose(1, 2).reshape(1, -1, a.config.num_attention_heads*a.head_dim)
        residual = buffer[:, start:stop] + a.o_proj(result)
        return residual, layer.post_attention_layernorm(residual)


class ConvolutionGraph(torch.nn.Module):
    def __init__(self, convolution):
        super().__init__()
        self.convolution = convolution

    def forward(self, input):
        c = self.convolution
        return F.conv1d(input, c.weight, c.bias, stride=c.stride,
                        dilation=c.dilation, groups=c.groups)


def export(size, directory):
    import onnx
    import onnxruntime as ort
    from onnxruntime.quantization import quantize_dynamic, QuantType
    from .model import load
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    torch.manual_seed(73)
    model, _, metadata = load(size, 'cpu', threads=2)
    encoder = model.model.encoder
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    errors = {}
    with torch.inference_mode():
        for i, layer in enumerate(encoder.layers):
            graph = AttentionGraph(layer)
            buffer = torch.randn(1, 25, encoder.config.hidden_size)
            args = (buffer, torch.tensor(3), torch.tensor(20), torch.ones(1, 1, 17, 25, dtype=torch.bool))
            name = 'attention-'+str(i)
            path = directory/(name+'-float.onnx')
            torch.onnx.export(graph, args, str(path), opset_version=18, dynamo=False,
                input_names=['buffer', 'start', 'stop', 'mask'], output_names=['residual', 'normalized'],
                dynamic_axes={'buffer': {1: 'frames'}, 'mask': {2: 'queries', 3: 'frames'},
                              'residual': {1: 'queries'}, 'normalized': {1: 'queries'}})
            onnx.checker.check_model(str(path))
            session = ort.InferenceSession(str(path), sess_options=options, providers=['CPUExecutionProvider'])
            maximum = 0.
            for frames, start, stop in ((1, 0, 1), (19, 4, 17), (41, 0, 37)):
                b = torch.randn(1, frames, encoder.config.hidden_size)
                left, right = encoder.config.sliding_windows[i]
                distance = torch.arange(start, stop)[:, None]-torch.arange(frames)[None]
                allowed = (((distance >= 0) & (distance < left)) |
                           ((distance < 0) & (-distance < right)))[None, None]
                expected = graph(b, torch.tensor(start), torch.tensor(stop), allowed)
                actual = session.run(None, {'buffer': b.numpy(), 'start': np.array(start, np.int64),
                    'stop': np.array(stop, np.int64), 'mask': allowed.numpy()})
                for a, e in zip(actual, expected):
                    # SDPA export rescales Q/K before GEMM, while Torch may
                    # scale its scores afterward. Trained projection weights
                    # amplify that float32 ordering difference near zero.
                    np.testing.assert_allclose(a, e.numpy(), rtol=3e-3, atol=3e-4)
                    maximum = max(maximum, float(np.max(np.abs(a-e.numpy()))))
            errors[name] = maximum
            del session
            quantize_dynamic(str(path), str(directory/(name+'-int8.onnx')),
                per_channel=True, weight_type=QuantType.QInt8, op_types_to_quantize=['MatMul', 'Gemm'])
        for i, c in enumerate((encoder.embedder.conv1, encoder.embedder.conv2)):
            graph = ConvolutionGraph(c)
            path = directory/('conv'+str(i)+'-float.onnx')
            x = torch.randn(1, c.in_channels, 37)
            torch.onnx.export(graph, (x,), str(path), opset_version=18, dynamo=False,
                input_names=['input'], output_names=['output'],
                dynamic_axes={'input': {2: 'frames'}, 'output': {2: 'outframes'}})
            onnx.checker.check_model(str(path))
            session = ort.InferenceSession(str(path), sess_options=options, providers=['CPUExecutionProvider'])
            maximum = 0.
            for frames in (c.kernel_size[0], 37, 102):
                x = torch.randn(1, c.in_channels, frames)
                actual, = session.run(None, {'input': x.numpy()})
                expected = graph(x).numpy()
                np.testing.assert_allclose(actual, expected, rtol=3e-4, atol=3e-5)
                maximum = max(maximum, float(np.max(np.abs(actual-expected))))
            errors['conv'+str(i)] = maximum
    manifest = {'format_version': 1, 'model': metadata['model'], 'revision': metadata['revision'],
        'layers': len(encoder.layers), 'hidden_size': encoder.config.hidden_size,
        'export_sources_sha256': metadata['runtime_sources_sha256'],
        'torch_version': torch.__version__, 'onnxruntime_version': ort.__version__,
        'float_max_errors': errors,
        'files': {p.name: digest(p) for p in sorted(directory.glob('*.onnx'))}}
    (directory/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps(manifest, indent=2))


class Runtime:
    def __init__(self, directory, model_metadata, threads=4, precision='int8', scheduling='sleep'):
        import onnxruntime as ort
        if threads < 1 or precision not in ('float', 'int8'):
            raise ValueError('Invalid native encoder configuration')
        directory = Path(directory)
        manifest = json.loads((directory/'manifest.json').read_text())
        if manifest['format_version'] != 1 or any(manifest[k] != model_metadata[k] for k in ('model', 'revision')):
            raise ValueError('Encoder artifacts do not match the pinned model')
        if model_metadata['scope'] != 'encoder-ffn':
            raise ValueError('Native encoder requires --scope encoder-ffn')
        opts = session_options(threads, scheduling)
        self.sessions = {}
        names = [(f'attention-{i}', precision) for i in range(manifest['layers'])]
        names += [(f'conv{i}', 'float') for i in range(2)]
        for name, policy in names:
            filename = name+'-'+policy+'.onnx'
            if digest(directory/filename) != manifest['files'][filename]:
                raise ValueError('Encoder artifact hash mismatch: '+filename)
            self.sessions[name] = ort.InferenceSession(str(directory/filename), sess_options=opts,
                                                       providers=['CPUExecutionProvider'])
        self.metadata = {'engine': 'onnxruntime', 'attention_precision': precision,
            'convolution_precision': 'float32', 'threads': threads, 'version': ort.__version__,
            'scheduling': scheduling,
            'manifest_sha256': digest(directory/'manifest.json')}

    def attention(self, index, buffer, start, stop, mask):
        residual, normalized = self.sessions[f'attention-{index}'].run(None, {
            'buffer': buffer.contiguous().numpy(), 'start': np.array(start, np.int64),
            'stop': np.array(stop, np.int64), 'mask': mask.numpy()})
        return torch.from_numpy(residual), torch.from_numpy(normalized)

    def convolution(self, index, input):
        result, = self.sessions[f'conv{index}'].run(None, {'input': input.contiguous().numpy()})
        return torch.from_numpy(result)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--size', choices=('small', 'tiny'), default='small')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    export(a.size, a.out)
