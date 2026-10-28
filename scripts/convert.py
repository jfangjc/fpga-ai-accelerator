# Co-authored by AI
# Convert the FP32 checkpoint to INT8 and write the model files:
#   data/int8.pkl                     the INT8 model, for run.py
#   data/export/quartznet_int8.bin    the QNW1 weights the board loads
#   data/export/running_prior.json    the board's starting normalisation statistics
# Calibration sets each tensor's INT8 scale from the first 100 LibriSpeech
# dev-clean clips run through the FP32 network: the 99.99th percentile of
# |activation|. The normalisation prior is those clips' log-mel statistics.
# This takes about 20 minutes.

import io
import json
import pathlib
import pickle
import struct
import tarfile
import numpy
import soundfile
import torch
import yaml
from frontend import features, log_mel, mel_power, valid_frames
from golden_int8 import checked, conv_frames, round_away

COUNT = 100
PERCENTILE = 99.99

# The FP32 network as a list of convolutions (batch norm folded in) and residual adds
def load_layers(path="data/quartznet.nemo"):
    with tarfile.open(path) as archive:
        config = yaml.safe_load(archive.extractfile("./model_config.yaml"))
        weights = archive.extractfile("./model_weights.ckpt").read()
    sd = torch.load(io.BytesIO(weights), map_location="cpu", weights_only=True)
    layers = []

    def conv(name, source, key, groups, stride, dilation, bn=None, relu=False):
        w = sd[key + ".weight"].clone()
        b = sd.get(key + ".bias", torch.zeros(w.shape[0])).clone()
        if bn:
            scale = sd[bn + ".weight"] / torch.sqrt(sd[bn + ".running_var"] + 1e-3)
            b = (b - sd[bn + ".running_mean"]) * scale + sd[bn + ".bias"]
            w = w * scale[:, None, None]
        padding = dilation * (w.shape[2] - 1) // 2
        layers.append(dict(name=name, op="conv", inputs=[source], w=w, b=b, groups=groups,
                           stride=stride, dilation=dilation, padding=padding, relu=relu))
        return name

    source, channels = "input", 64
    for b, block in enumerate(config["encoder"]["jasper"]):
        block_input = source
        prefix = f"encoder.encoder.{b}."
        separable = block.get("separable", False)
        step = 5 if separable else 4    # NeMo modules per repeat
        for r in range(block["repeat"]):
            key = f"{prefix}mconv.{r * step}.conv"
            if separable:
                stride = block["stride"][0] if r == 0 else 1
                source = conv(f"b{b}.r{r}.dw", source, key, channels, stride, block["dilation"][0])
                key = f"{prefix}mconv.{r * step + 1}.conv"
            bn = f"{prefix}mconv.{r * step + (2 if separable else 1)}"
            # A residual block's last convolution feeds the add, without ReLU
            relu = not (r == block["repeat"] - 1 and block["residual"])
            source = conv(f"b{b}.r{r}.pw", source, key, 1, 1, 1, bn, relu)
            channels = block["filters"]
        if block["residual"]:
            res = conv(f"b{b}.res", block_input, prefix + "res.0.0.conv", 1, 1, 1, prefix + "res.0.1")
            layers.append(dict(name=f"b{b}.add", op="add", inputs=[source, res], relu=True))
            source = f"b{b}.add"
    conv("logits", source, "decoder.decoder_layers.0", 1, 1, 1)
    return layers

# Every layer's FP32 output by name, and the logits' valid length
@torch.inference_mode()
def forward(layers, features, length):
    tensors = {"input": torch.as_tensor(features, dtype=torch.float32)[None]}
    lengths = {"input": length}
    for l in layers:
        valid = lengths[l["inputs"][0]]
        if l["op"] == "add":
            y = tensors[l["inputs"][0]] + tensors[l["inputs"][1]]
        else:
            x = tensors[l["inputs"][0]].clone()
            x[:, :, valid:] = 0
            y = torch.nn.functional.conv1d(x, l["w"], l["b"], l["stride"], l["padding"], l["dilation"], l["groups"])
            valid = conv_frames(valid, l["w"].shape[2], l["stride"], l["padding"], l["dilation"])
        if l["relu"]:
            y = y.relu()
        tensors[l["name"]] = y
        lengths[l["name"]] = valid
    return tensors, valid

# Calibration: each tensor's scale and the normalisation prior, from dev-clean
def calibrate(layers):
    files = sorted(pathlib.Path("data/LibriSpeech/dev-clean").rglob("*.flac"), key=lambda path: path.stem)[:COUNT]
    ranges = {}
    log_sum = numpy.zeros(64)
    log_sumsq = numpy.zeros(64)
    frames = 0
    for path in files:
        audio, _ = soundfile.read(path, dtype="float32")
        logs = log_mel(mel_power(audio)).astype(numpy.float64)
        log_sum += logs.sum(1)
        log_sumsq += (logs**2).sum(1)
        frames += logs.shape[1]
        tensors, _ = forward(layers, features(audio), valid_frames(audio))
        for name, x in tensors.items():
            ranges[name] = max(ranges.get(name, 0), float(numpy.percentile(numpy.abs(x.numpy()), PERCENTILE)))
    # ReLU outputs use the UINT8 range, everything else INT8
    scales = {"input": max(ranges["input"], 1e-8) / 127}
    for l in layers:
        scales[l["name"]] = max(ranges[l["name"]], 1e-8) / (255 if l["relu"] else 127)
    # The prior is the dev-clean log-mel mean and variance, weighted as COUNT frames
    mean = log_sum / frames
    variance = numpy.maximum(log_sumsq / frames - mean**2, 1e-8)
    prior = dict(count=COUNT, mean=mean, m2=variance * (COUNT - 1))
    return scales, prior, [path.stem for path in files]

# Fixed-point scale: ratio = mult * 2^-(31+shift), mult in [2^30, 2^31)
def q31(ratio):
    mantissa, exponent = numpy.frexp(numpy.asarray(ratio, dtype=numpy.float64))
    mult = round_away(mantissa * 2**31)
    overflow = mult == 2**31
    mult = numpy.where(overflow, mult // 2, mult)
    return mult.astype(numpy.int32), (-(exponent + overflow)).astype(numpy.int32)

# INT8 weights, INT32 biases and the requantisation multipliers of every layer
def quantize(layers, scales, prior, ids):
    quantized = []
    for l in layers:
        q = {k: v for k, v in l.items() if k not in ("w", "b")}
        scale = scales[l["name"]]
        if l["op"] == "conv":
            w = l["w"].numpy()
            b = l["b"].numpy()
            x_scale = scales[l["inputs"][0]]
            # Per-channel weight scale. A nearly-zero batch-norm channel can carry
            # a large bias, so keep the bias within half the int32 accumulator
            w_scale = numpy.maximum(numpy.maximum(numpy.abs(w).max((1, 2)) / 127, 1e-12), numpy.abs(b) / (x_scale * 2**30))
            q["w"] = numpy.clip(round_away(w / w_scale[:, None, None]), -127, 127).astype(numpy.int8)
            q["b"] = checked(round_away(b / (x_scale * w_scale)))
            q["mult"], q["shift"] = q31(x_scale * w_scale / scale)
            channels = len(b)
        else:
            # Residual add: rescale both branches to this layer's scale
            for i, branch in enumerate(l["inputs"]):
                q[f"mult{i}"], q[f"shift{i}"] = q31(numpy.full(channels, scales[branch] / scale))
        q["scale"] = scale
        quantized.append(q)
    return dict(input_scale=scales["input"], layers=quantized, calibration_ids=ids,
                percentile=PERCENTILE, running_prior=prior)

# Weights [O, C, K] as [O/32][C*K/16][16][32], zero-padded
def tiled(w):
    w = w.reshape(w.shape[0], -1)
    w = numpy.pad(w, ((0, -w.shape[0] % 32), (0, -w.shape[1] % 16)))
    return w.reshape(w.shape[0] // 32, 32, w.shape[1] // 16, 16).transpose(0, 2, 3, 1).copy()

# QNW1: a 64-byte header, a 128-byte descriptor per layer, then the arrays,
# each 64-byte aligned. Descriptor words: 0 op (1 conv, 2 add), 1-2 inputs,
# 3 ReLU, 4-10 convolution shape, 12-19 offset and size of four arrays
def qnw1(model):
    layers = model["layers"]
    base = 64 + 128 * len(layers)
    payload = bytearray()
    descriptors = []
    ids = {"input": 0}
    for i, l in enumerate(layers, 1):
        ids[l["name"]] = i
        words = [0] * 32
        words[1] = ids[l["inputs"][0]]
        words[3] = int(l["relu"])
        if l["op"] == "conv":
            words[0] = 1
            words[4:11] = [*l["w"].shape, l["stride"], l["dilation"], l["padding"], l["groups"]]
            arrays = [tiled(l["w"]), l["b"], l["mult"], l["shift"]]
        else:
            words[0] = 2
            words[2] = ids[l["inputs"][1]]
            words[4] = len(l["mult0"])
            arrays = [l["mult0"], l["shift0"], l["mult1"], l["shift1"]]
        for j, array in enumerate(arrays):
            payload += bytes(-(base + len(payload)) % 64)
            words[12 + 2 * j] = base + len(payload)
            words[13 + 2 * j] = array.nbytes
            payload += array.tobytes()
        descriptors.append(struct.pack("<32I", *words))
    header = struct.pack("<4s7If", b"QNW1", 1, 64, len(layers), 128, 64, base + len(payload), 0, model["input_scale"])
    return header.ljust(64, b'\0') + b"".join(descriptors) + payload

def convert():
    torch.set_num_threads(4)
    layers = load_layers()
    scales, prior, ids = calibrate(layers)
    model = quantize(layers, scales, prior, ids)

    pathlib.Path("data/int8.pkl").write_bytes(pickle.dumps(model))
    export = pathlib.Path("data/export")
    export.mkdir(exist_ok=True)
    (export / "quartznet_int8.bin").write_bytes(qnw1(model))
    prior = {k: v if k == "count" else v.tolist() for k, v in prior.items()}
    (export / "running_prior.json").write_text(json.dumps(prior, indent=2), newline="\n")
