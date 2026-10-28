# Co-authored by AI
# INT8 golden network and CTC decoder, the integer arithmetic that the PL engine
# and the CPU runtime reproduce bit for bit. Tensors are [channels, frames]
from itertools import groupby
import numpy

VOCAB = " abcdefghijklmnopqrstuvwxyz'"    # class 28 is the CTC blank

# Round half away from zero
def round_away(x):
    return (numpy.sign(x) * numpy.floor(numpy.abs(x) + .5)).astype(numpy.int64)

def quantize(features, scale):
    return numpy.clip(round_away(features / scale), -128, 127).astype(numpy.int8)

# int64 to int32, failing instead of wrapping
def checked(acc):
    if acc.min() < -2**31 or acc.max() >= 2**31:
        raise OverflowError("int32 accumulator")
    return acc.astype(numpy.int32)

def conv_frames(frames, taps, stride, padding, dilation):
    return (frames + 2 * padding - dilation * (taps - 1) - 1) // stride + 1

# Scale each channel by mult * 2^-(31+shift) with one rounding, then clamp
def requant(acc, mult, shift, lo, hi):
    product = acc.astype(numpy.int64) * numpy.asarray(mult, numpy.int64)[:, None]
    n = 31 + numpy.asarray(shift, numpy.int64)[:, None]
    s = numpy.minimum(n, 63)
    q = numpy.sign(product) * ((numpy.abs(product) + (numpy.int64(1) << (s - 1))) >> s)
    q = numpy.where(n >= 64, 0, q)
    return numpy.clip(q, lo, hi)

# Integer convolution plus bias, dense (groups 1) or depthwise
def conv_acc(x, w, b, stride, padding, dilation, groups):
    out_channels, _, taps = w.shape
    frames = conv_frames(x.shape[1], taps, stride, padding, dilation)
    x = numpy.pad(x.astype(numpy.int64), ((0, 0), (padding, padding)))
    acc = numpy.zeros((out_channels, frames), numpy.int64)
    for j in range(taps):
        window = x[:, j * dilation:j * dilation + frames * stride:stride]
        if groups == 1:
            acc += w[:, :, j].astype(numpy.int64) @ window
        else:
            acc += w[:, 0, j, None].astype(numpy.int64) * window
    return checked(acc + numpy.asarray(b, numpy.int64)[:, None])

# Logits [29, valid frames] for INT8 input [64, frames] with `length` real frames
def network(x, layers, length):
    tensors = {"input": x}
    lengths = {"input": length}
    for l in layers:
        valid = lengths[l["inputs"][0]]
        if l["op"] == "add":
            # Each branch is rescaled to the add's scale and clamped to INT16,
            # then the sum is clamped once to UINT8
            a = requant(tensors[l["inputs"][0]], l["mult0"], l["shift0"], -2**15, 2**15 - 1)
            b = requant(tensors[l["inputs"][1]], l["mult1"], l["shift1"], -2**15, 2**15 - 1)
            y = numpy.clip(a + b, 0, 255).astype(numpy.uint8)
        else:
            x = tensors[l["inputs"][0]].copy()
            x[:, valid:] = 0
            acc = conv_acc(x, l["w"], l["b"], l["stride"], l["padding"], l["dilation"], l["groups"])
            if l["relu"]:
                y = requant(acc, l["mult"], l["shift"], 0, 255).astype(numpy.uint8)
            else:
                y = requant(acc, l["mult"], l["shift"], -128, 127).astype(numpy.int8)
            valid = conv_frames(valid, l["w"].shape[2], l["stride"], l["padding"], l["dilation"])
        tensors[l["name"]] = y
        lengths[l["name"]] = valid
    return y[:, :valid]

# CTC greedy decode: best class per frame, collapse repeats, drop blanks
def greedy(logits):
    classes = numpy.asarray(logits).argmax(axis=0)
    return "".join(VOCAB[c] for c, _ in groupby(classes) if c < len(VOCAB)).strip()
