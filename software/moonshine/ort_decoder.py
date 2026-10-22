"""Optional native CPU decoder paired with the physical FPGA encoder.

Export uses the pinned float checkpoint. Runtime artifacts are hash checked; no
decoder cache survives an encoder-memory update. PyTorch remains the reference.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import torch


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def session_options(threads, scheduling='sleep'):
    import onnxruntime as ort
    if threads < 1 or scheduling not in ('sleep', 'run-spin'):
        raise ValueError('Invalid native runtime scheduling configuration')
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.add_session_config_entry('session.intra_op.allow_spinning', '1' if scheduling == 'run-spin' else '0')
    if scheduling == 'run-spin':
        # Keep workers ready between kernels within Run(), then stop them as
        # soon as it returns so they do not compete with FPGA DMA or Torch.
        options.add_session_config_entry('session.force_spinning_stop', '1')
    return options


class MemoryGraph(torch.nn.Module):
    def __init__(self, decoder):
        super().__init__()
        self.decoder = decoder

    def forward(self, memory):
        decoder = self.decoder
        memory = decoder.proj(memory + decoder.pos_emb(torch.arange(memory.shape[1])))
        keys, values = [], []
        for layer in decoder.layers:
            a = layer.encoder_attn
            keys.append(a.k_proj(memory).reshape(1, -1, a.config.num_key_value_heads, a.head_dim).transpose(1, 2))
            values.append(a.v_proj(memory).reshape(1, -1, a.config.num_key_value_heads, a.head_dim).transpose(1, 2))
        return torch.stack(keys), torch.stack(values)


class StepGraph(torch.nn.Module):
    def __init__(self, model):
        super().__init__()
        self.decoder, self.proj_out = model.model.decoder, model.proj_out
        if self.decoder.rotary_emb.rope_type != 'default':
            raise ValueError('Native decoder supports the pinned default rotary embedding only')

    @staticmethod
    def rotary(x, cos, sin, dim):
        even, odd = x[..., :dim:2], x[..., 1:dim:2]
        rotated = torch.stack((even*cos - odd*sin, odd*cos + even*sin), dim=-1)
        rotated = rotated.reshape(x.shape[0], x.shape[1], x.shape[2], dim)
        return torch.cat((rotated, x[..., dim:]), dim=-1)

    @staticmethod
    def attention(a, query, keys, values, mask=None):
        groups = a.num_key_value_groups
        if groups != 1:
            keys = keys.repeat_interleave(groups, dim=1)
            values = values.repeat_interleave(groups, dim=1)
        # The pinned model's head-padding path preserves the original scaling.
        # Explicit matmul/softmax exports portably and avoids SDPA tracing masks.
        scores = torch.matmul(query, keys.transpose(-1, -2)) * a.scaling
        if mask is not None:
            scores = scores + mask
        result = torch.matmul(torch.softmax(scores, dim=-1), values)
        return a.o_proj(result.transpose(1, 2).reshape(1, query.shape[2], -1))

    def forward(self, input_ids, positions, mask, past_keys, past_values, cross_keys, cross_values):
        d = self.decoder
        hidden = d.embed_tokens(input_ids)
        frequencies = positions[..., None].float() * d.rotary_emb.inv_freq
        cos, sin = frequencies.cos()[:, None], frequencies.sin()[:, None]
        rotary_dim = d.rotary_emb.inv_freq.shape[0] * 2
        keys_out, values_out = [], []
        for i, layer in enumerate(d.layers):
            a = layer.self_attn
            norm = layer.input_layernorm(hidden)
            def heads(x):
                return x.reshape(1, -1, a.config.num_key_value_heads, a.head_dim).transpose(1, 2)
            query = self.rotary(heads(a.q_proj(norm)), cos, sin, rotary_dim)
            key = self.rotary(heads(a.k_proj(norm)), cos, sin, rotary_dim)
            key = torch.cat((past_keys[i], key), dim=2)
            value = torch.cat((past_values[i], heads(a.v_proj(norm))), dim=2)
            keys_out.append(key); values_out.append(value)
            hidden = hidden + self.attention(a, query, key, value, mask)
            a = layer.encoder_attn
            query = heads(a.q_proj(layer.post_attention_layernorm(hidden)))
            hidden = hidden + self.attention(a, query, cross_keys[i], cross_values[i])
            hidden = hidden + layer.mlp(layer.final_layernorm(hidden))
        logits = self.proj_out(d.norm(hidden))
        return logits, torch.stack(keys_out), torch.stack(values_out)


def export(size, directory):
    import onnx
    import onnxruntime as ort
    from onnxruntime.quantization import quantize_dynamic, QuantType
    from .model import load
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    model, _, metadata = load(size, 'cpu', threads=2)
    d = model.model.decoder
    layers, heads, dim = len(d.layers), d.config.num_key_value_heads, d.layers[0].self_attn.head_dim
    memory = torch.randn(1, 13, model.config.encoder_config.hidden_size)
    with torch.inference_mode():
        cross = MemoryGraph(d)(memory)
        past = torch.zeros(layers, 1, heads, 2, dim)
        args = (torch.tensor([[1, 3, 4]]), torch.tensor([[2, 3, 4]]),
                torch.zeros(1, 1, 3, 5), past, past, *cross)
        torch.onnx.export(MemoryGraph(d), (memory,), str(directory/'memory-float.onnx'),
            input_names=['memory'], output_names=['cross_keys', 'cross_values'], opset_version=18,
            dynamic_axes={'memory': {1: 'frames'}, 'cross_keys': {3: 'frames'}, 'cross_values': {3: 'frames'}},
            dynamo=False)
        torch.onnx.export(StepGraph(model), args, str(directory/'step-float.onnx'),
            input_names=['input_ids', 'positions', 'mask', 'past_keys', 'past_values', 'cross_keys', 'cross_values'],
            output_names=['logits', 'keys', 'values'], opset_version=18, dynamo=False,
            dynamic_axes={'input_ids': {1: 'tokens'}, 'positions': {1: 'tokens'},
                'mask': {2: 'tokens', 3: 'total'}, 'past_keys': {3: 'past'}, 'past_values': {3: 'past'},
                'cross_keys': {3: 'frames'}, 'cross_values': {3: 'frames'},
                'logits': {1: 'tokens'}, 'keys': {3: 'total'}, 'values': {3: 'total'}})
    for name in ('memory', 'step'):
        onnx.checker.check_model(str(directory/(name+'-float.onnx')))
        quantize_dynamic(str(directory/(name+'-float.onnx')), str(directory/(name+'-int8.onnx')),
            per_channel=True, weight_type=QuantType.QInt8, op_types_to_quantize=['MatMul', 'Gemm'])
    # Export-time tensor conformance includes zero-length cache and nonzero past.
    opts = ort.SessionOptions(); opts.intra_op_num_threads = 2; opts.inter_op_num_threads = 1
    session = ort.InferenceSession(str(directory/'step-float.onnx'), sess_options=opts,
                                   providers=['CPUExecutionProvider'])
    errors = []
    with torch.inference_mode():
        for length, old, frames in ((1, 0, 1), (4, 0, 13), (1, 7, 25), (3, 2, 17)):
            mem = torch.randn(1, frames, model.config.encoder_config.hidden_size)
            ck, cv = MemoryGraph(d)(mem)
            pk, pv = (torch.randn(layers, 1, heads, old, dim) for _ in range(2))
            q = torch.arange(old, old+length)[:, None]; k = torch.arange(old+length)[None]
            mask = torch.where(k <= q, 0., -float('inf'))[None, None]
            inputs = (torch.randint(0, model.config.vocab_size, (1, length)),
                      torch.arange(old, old+length)[None], mask, pk, pv, ck, cv)
            expected = StepGraph(model)(*inputs)
            actual = session.run(None, dict(zip((i.name for i in session.get_inputs()),
                                               (x.numpy() for x in inputs))))
            for a, e in zip(actual, expected):
                np.testing.assert_allclose(a, e.numpy(), rtol=3e-3, atol=3e-4)
            errors.append(float(np.max(np.abs(actual[0]-expected[0].numpy()))))
        # Independently check the custom graph against the pinned HF dataflow,
        # including its causal prefill, rotary convention, and cached token step.
        from .streaming import DecoderUpdate
        reference = DecoderUpdate(d, memory)
        cache = None
        pk = pv = torch.empty(layers, 1, heads, 0, dim)
        for ids in (torch.tensor([[1, 17, 31]]), torch.tensor([[42]]), torch.tensor([[19, 7]])):
            old, n = pk.shape[3], ids.shape[1]
            q = torch.arange(old, old+n)[:, None]; k = torch.arange(old+n)[None]
            mask = torch.where(k <= q, 0., -float('inf'))[None, None]
            hidden, cache = reference(ids, cache)
            actual, pk, pv = StepGraph(model)(ids, torch.arange(old, old+n)[None], mask, pk, pv, *cross)
            torch.testing.assert_close(actual, model.proj_out(hidden), rtol=3e-3, atol=3e-4)
    manifest = {'format_version': 1, 'model': metadata['model'], 'revision': metadata['revision'],
        'layers': layers, 'heads': heads, 'head_dim': dim, 'torch_version': torch.__version__,
        'onnxruntime_version': ort.__version__, 'export_sources_sha256': metadata['runtime_sources_sha256'],
        'float_logits_max_errors': errors,
        'files': {p.name: digest(p) for p in sorted(directory.glob('*.onnx'))}}
    (directory/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps(manifest, indent=2))


class Cache:
    def __init__(self, keys, values, profile=None):
        self.keys, self.values = keys, values
        self.profile = profile

    def get_seq_length(self):
        return self.keys.shape[3]

    def crop(self, amount):
        if amount >= 0:
            raise ValueError('Decoder cache crop expects a negative removal count')
        start = time.perf_counter()
        self.keys = self.keys[:, :, :, :amount, :].copy()
        self.values = self.values[:, :, :, :amount, :].copy()
        if self.profile is not None:
            self.profile['cache_crop_seconds'] += time.perf_counter()-start


class Runtime:
    def __init__(self, directory, model_metadata, threads=2, precision='int8', scheduling='sleep',
                 step_threads=None, single_token=True):
        import onnxruntime as ort
        directory = Path(directory)
        self.manifest = json.loads((directory/'manifest.json').read_text())
        if self.manifest['format_version'] != 1 or any(self.manifest[k] != model_metadata[k] for k in ('model', 'revision')):
            raise ValueError('Decoder artifacts do not match the pinned model')
        opts = session_options(threads, scheduling)
        self.sessions = {}
        for name in ('memory', 'step'):
            filename = name+'-'+precision+'.onnx'
            if digest(directory/filename) != self.manifest['files'][filename]:
                raise ValueError('Decoder artifact hash mismatch: '+filename)
            self.sessions[name] = ort.InferenceSession(str(directory/filename), sess_options=opts,
                                                       providers=['CPUExecutionProvider'])
        filename = 'single-'+precision+'.onnx'
        self.single_specialized = single_token and filename in self.manifest['files']
        if step_threads is not None and step_threads < 1:
            raise ValueError('Single-token thread count must be positive')
        if self.single_specialized or step_threads is not None:
            filename = filename if self.single_specialized else 'step-'+precision+'.onnx'
            if digest(directory/filename) != self.manifest['files'][filename]:
                raise ValueError('Single-token decoder artifact hash mismatch')
            self.sessions['single'] = ort.InferenceSession(str(directory/filename),
                sess_options=session_options(step_threads or threads, scheduling), providers=['CPUExecutionProvider'])
        self.metadata = {'engine': 'onnxruntime', 'precision': precision, 'version': ort.__version__,
                         'manifest_sha256': digest(directory/'manifest.json'), 'threads': threads,
                         'scheduling': scheduling}
        if 'single' in self.sessions:
            self.metadata.update(single_token_graph='specialized' if self.single_specialized else 'generic',
                                 single_token_threads=step_threads or threads)
        self.profile = {'memory_seconds': 0., 'step_seconds': 0., 'argmax_seconds': 0.,
                        'step_calls': 0, 'step_tokens': 0, 'single_seconds': 0., 'single_calls': 0,
                        'prefill_seconds': 0., 'prefill_calls': 0, 'input_seconds': 0., 'cache_crop_seconds': 0.}

    def update(self, memory):
        return Update(self, memory)


class Update:
    def __init__(self, runtime, memory):
        self.runtime = runtime
        start = time.perf_counter()
        self.cross_keys, self.cross_values = runtime.sessions['memory'].run(None, {'memory': memory.numpy()})
        runtime.profile['memory_seconds'] += time.perf_counter()-start

    def predict(self, input_ids, cache=None):
        runtime = self.runtime
        begin = time.perf_counter()
        n = input_ids.shape[1]
        if cache is None:
            m = runtime.manifest
            shape = (m['layers'], 1, m['heads'], 0, m['head_dim'])
            cache = Cache(np.empty(shape, np.float32), np.empty(shape, np.float32))
        old = cache.get_seq_length()
        positions = np.arange(old, old+n, dtype=np.int64)[None]
        single = n == 1 and 'single' in runtime.sessions
        feeds = {'input_ids': input_ids.numpy(), 'positions': positions,
            'past_keys': cache.keys, 'past_values': cache.values,
            'cross_keys': self.cross_keys, 'cross_values': self.cross_values}
        if not (single and runtime.single_specialized):
            feeds['mask'] = np.where(np.arange(old+n)[None] <= positions.T,
                np.float32(0), np.float32(-np.inf))[None, None]
        start = time.perf_counter()
        runtime.profile['input_seconds'] += start-begin
        logits, keys, values = runtime.sessions['single' if single else 'step'].run(None, feeds)
        end = time.perf_counter()
        predictions = logits.argmax(axis=-1)[0].tolist()
        runtime.profile['step_seconds'] += end-start
        kind = 'single' if n == 1 else 'prefill'
        runtime.profile[kind+'_seconds'] += end-start
        runtime.profile[kind+'_calls'] += 1
        runtime.profile['argmax_seconds'] += time.perf_counter()-end
        runtime.profile['step_calls'] += 1
        runtime.profile['step_tokens'] += n
        return predictions, Cache(keys, values, runtime.profile)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--size', choices=('small', 'tiny'), default='tiny')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    export(a.size, a.out)
