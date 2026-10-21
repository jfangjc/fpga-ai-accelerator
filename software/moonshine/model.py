"""Pinned model loading and explicit CPU/INT8-oracle/FPGA routing."""
import hashlib
from pathlib import Path
import torch
from .matrix import Projection

MODELS = {
    'small': ('moonshine-ai/moonshine-streaming-small', '2c036506f23a09c18df5a50057599ba6d9280999'),
    'tiny': ('moonshine-ai/moonshine-streaming-tiny', 'f8e9dfd8c562c257c151a907b7b7f2fe8ff8511a'),
}


def source_hash():
    value = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob('*.py')):
        value.update(path.name.encode())
        value.update(path.read_bytes())
    for name in ('qnc.h', 'qnc_transport.c'):
        value.update((Path(__file__).parents[1]/'ort_fpga_stream_branch512_neon'/name).read_bytes())
    value.update(Path(__file__).with_name('voice_capture.c').read_bytes())
    return value.hexdigest()


class Linear(torch.nn.Module):
    def __init__(self, original, device):
        super().__init__()
        self.projection = Projection(original.weight.detach().numpy(),
                                     None if original.bias is None else original.bias.detach().numpy(), device)

    def forward(self, x):
        if x.device.type != 'cpu' or x.dtype != torch.float32 or torch.is_grad_enabled():
            raise RuntimeError('FPGA/oracle projections require CPU float32 inference_mode')
        return torch.from_numpy(self.projection(x.detach().contiguous().numpy()))


class Convolution(torch.nn.Module):
    """Lower a causal frontend convolution to a projection over input windows."""
    def __init__(self, conv):
        super().__init__()
        if conv.groups != 1 or conv.dilation != (1,):
            raise ValueError('Moonshine frontend lowering requires dense, undilated convolutions')
        self.left_pad, self.stride = conv.left_pad, conv.stride
        self.out_channels, self.kernel_size = conv.out_channels, conv.kernel_size
        self.projection = torch.nn.Linear(conv.in_channels * conv.kernel_size[0], conv.out_channels,
                                         bias=conv.bias is not None)
        self.projection.weight = torch.nn.Parameter(conv.weight.detach().flatten(1), requires_grad=False)
        if conv.bias is not None:
            self.projection.bias = torch.nn.Parameter(conv.bias.detach(), requires_grad=False)

    def project_valid(self, x):
        windows = x.unfold(-1, self.kernel_size[0], self.stride[0])
        windows = windows.permute(0, 2, 1, 3).contiguous().flatten(2)
        return self.projection(windows).transpose(1, 2)

    def forward(self, x, mask=None):
        x = torch.nn.functional.pad(x, (self.left_pad, 0))
        out = self.project_valid(x)
        if mask is not None:
            mask = torch.nn.functional.pad(mask, (self.left_pad, 0))[:, None]
            mask = torch.nn.functional.conv1d(mask.float(), torch.ones((1, 1, self.kernel_size[0])),
                                              stride=self.stride) > 0
            out = out * mask
            mask = mask.squeeze(1)
        return out, mask


def load(size='small', backend='cpu', device=None, scope='encoder-ffn', threads=2, cpu_tail_int8=True):
    from transformers import AutoProcessor, MoonshineStreamingForConditionalGeneration
    if backend not in ('cpu', 'cpu-int8', 'oracle', 'fpga') or scope not in ('frontend-ffn', 'encoder-ffn', 'encoder', 'all'):
        raise ValueError('Invalid backend or projection scope')
    if backend == 'fpga' and device is None:
        raise ValueError('FPGA backend requires a physical device')
    torch.set_num_threads(threads)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass  # A caller may already have initialized the inter-op pool.
    name, revision = MODELS[size]
    processor = AutoProcessor.from_pretrained(name, revision=revision, trust_remote_code=False)
    model = MoonshineStreamingForConditionalGeneration.from_pretrained(
        name, revision=revision, attn_implementation='sdpa', dtype=torch.float32,
        trust_remote_code=False).eval()
    lower_frontend = backend in ('oracle', 'fpga') and scope in ('frontend-ffn', 'encoder', 'all')
    if lower_frontend:
        embedder = model.model.encoder.embedder
        embedder.conv1 = Convolution(embedder.conv1)
        embedder.conv2 = Convolution(embedder.conv2)
    replaced = []
    if backend == 'cpu-int8':
        torch.backends.quantized.engine = 'qnnpack'
        model = torch.ao.quantization.quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8)
    elif backend != 'cpu':
        for path, module in list(model.named_modules()):
            if not isinstance(module, torch.nn.Linear):
                continue
            selected = (scope == 'all' or (path.startswith('model.encoder.') and
                        (scope == 'encoder' or '.mlp.' in path or
                         (scope == 'frontend-ffn' and '.embedder.conv' in path))))
            if selected:
                parent, attr = path.rsplit('.', 1)
                setattr(model.get_submodule(parent), attr, Linear(module, device if backend == 'fpga' else None))
                replaced.append(path)
        if cpu_tail_int8:
            torch.backends.quantized.engine = 'qnnpack'
            # In-place conversion preserves the single physical device context.
            model = torch.ao.quantization.quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8, inplace=True)
    return model, processor, {'model': name, 'revision': revision, 'backend': backend,
                              'runtime_sources_sha256': source_hash(),
                              'scope': scope, 'projections': replaced, 'threads': threads,
                              'frontend_lowering': lower_frontend,
                              'cpu_tail_int8': cpu_tail_int8 if backend in ('oracle', 'fpga') else backend == 'cpu-int8'}
