"""Validated QNW1 reader and stream-ordered QNC2 program exporter."""
import argparse
from pathlib import Path
import struct


def align16(data):
    return data + bytes((-len(data)) % 16)


def read_qnw(data):
    if len(data) < 64:
        raise ValueError('short QNW1 header')
    magic, version, header, count, stride, offset, size, reserved = struct.unpack_from('<4s7I', data)
    if (magic, version, header, stride, offset, size, reserved) != (b'QNW1', 1, 64, 128, 64, len(data), 0):
        raise ValueError('invalid QNW1 header')
    if any(data[36:64]) or 64 + count*128 > size:
        raise ValueError('reserved header or truncated table')
    records = []
    for i in range(count):
        d = list(struct.unpack_from('<32I', data, 64+i*128))
        op, src, src1, relu, channels, ci, kernel, stride, dilation, padding, groups, zero = d[:12]
        if op not in (1, 2) or src > i or src1 > i or relu > 1 or not channels or zero or any(d[20:]):
            raise ValueError('invalid descriptor')
        if op == 1:
            if src1 or not ci or not kernel or not stride or not dilation or groups not in (1, channels):
                raise ValueError('invalid convolution')
            if groups != 1 and ci != 1:
                raise ValueError('invalid depthwise groups')
            lengths = [((channels+31)//32)*((ci*kernel+15)//16)*512, channels*4, channels*4, channels*4]
        else:
            if not relu or any(d[5:11]):
                raise ValueError('invalid add')
            lengths = [channels*4]*4
        arrays = []
        for slot, expected in zip(range(12, 20, 2), lengths):
            start, length = d[slot:slot+2]
            if start % 64 or start < 64+count*128 or length != expected or start+length > size:
                raise ValueError('invalid array bounds/size/alignment')
            arrays.append(data[start:start+length])
        for m, s in ((arrays[2], arrays[3]),) if op == 1 else ((arrays[0], arrays[1]), (arrays[2], arrays[3])):
            if any(v <= 0 for v in struct.unpack('<'+'i'*channels, m)) or any(v < -30 for v in struct.unpack('<'+'i'*channels, s)):
                raise ValueError('invalid requantization parameters')
        records.append((d, arrays))
    return records


def export(data):
    """Record parameters precede tiled weights; no absolute DDR references remain."""
    records = read_qnw(data)
    out = bytearray()
    for index, (d, arrays) in enumerate(records, 1):
        payload = b''.join(align16(a) for a in (arrays[1:]+arrays[:1] if d[0] == 1 else arrays))
        header = [0x32524C51, 64+len(payload), index, *d[:11], 0, 0]
        out.extend(struct.pack('<16I', *header))
        out.extend(payload)
    return struct.pack('<4s3I', b'QNP2', 1, len(records), 16+len(out)) + out


def read_program(data):
    """Validate a linear QNP2 stream without trusting record byte counts.

    Return (descriptor, arrays) in QNW1 logical order, so the existing layer
    encoder can also consume programs. Source ids are strictly backward.
    """
    if len(data) < 16:
        raise ValueError('short QNP2 header')
    magic, version, count, size = struct.unpack_from('<4s3I', data)
    if magic != b'QNP2' or version != 1 or size != len(data) or not count:
        raise ValueError('invalid QNP2 header')
    records, cursor = [], 16
    for index in range(1, count+1):
        if cursor+64 > size:
            raise ValueError('truncated QLR2 header')
        h = struct.unpack_from('<16I', data, cursor)
        d = list(h[3:14]) + [0]*21
        op, src, src1, relu, co, ci, k, stride, dilation, pad, groups = d[:11]
        if (h[0] != 0x32524c51 or h[2] != index or any(h[14:])
                or src >= index or src1 >= index or relu > 1
                or not 1 <= co <= 1024):
            raise ValueError('invalid QLR2 descriptor')
        if op == 1:
            pw = 1 <= ci <= 1024 and [k, stride, dilation, pad, groups] == [1, 1, 1, 0, 1]
            dw = ci == 1 and groups == co and 1 <= k <= 87 and stride in (1, 2) and dilation in (1, 2) and 0 <= pad <= 86
            if src1 or not (pw or dw):
                raise ValueError('unsupported convolution')
            lengths = [co*4]*3 + [((co+31)//32)*((ci*k+15)//16)*512]
        elif op == 2 and relu == 1 and not any(d[5:11]):
            lengths = [co*4]*4
        else:
            raise ValueError('unsupported operation')
        expected = 64+sum((n+15)//16*16 for n in lengths)
        if h[1] != expected or cursor+expected > size:
            raise ValueError('invalid QLR2 size')
        arrays, pos = [], cursor+64
        for n in lengths:
            arrays.append(data[pos:pos+n])
            if any(data[pos+n:pos+(n+15)//16*16]):
                raise ValueError('nonzero array padding')
            pos += (n+15)//16*16
        pairs = [(arrays[1], arrays[2])] if op == 1 else [(arrays[0], arrays[1]), (arrays[2], arrays[3])]
        for mult, shift in pairs:
            if (any(v <= 0 for v in struct.unpack('<'+'i'*co, mult))
                    or any(v < -30 for v in struct.unpack('<'+'i'*co, shift))):
                raise ValueError('invalid requantization parameters')
        records.append((d, arrays[-1:]+arrays[:-1] if op == 1 else arrays))
        cursor += expected
    if cursor != size:
        raise ValueError('trailing QNP2 data')
    return records


def run_network(program, features, steps, valid, *, tag=0):
    """Encode the RUN_NETWORK envelope for engine version 0x00010002.

    No program rewrite: QNP2 tensor ids and execution order are preserved.
    The fixed allocation is validated separately by network_allocation.
    """
    from engine_model import network_allocation
    records = read_program(program)
    network_allocation(records, steps, valid)
    if len(records) != 186 or len(features) != 64*steps or not 0 <= tag <= 0xffffffff:
        raise ValueError('unsupported network command')
    payload = align16(features)+program
    header = [0x32434e51, 1, 2, tag, len(payload), 64, steps, valid,
              29, 1, 186, len(program), 0, 0, 0, 0]
    return struct.pack('<16I', *header)+payload


def run_layer(record, features, steps, valid, *, signed=True, tag=0,
              branch1=None):
    """Encode an input-first layer command (S1 pointwise or proposed S2a).

    Input must precede weights: only two weight tiles fit on chip, and their
    consumption needs the complete activation tensor. Weight-first framing
    would deadlock on the third output tile.
    """
    d, arrays = record
    co = d[4]
    pointwise = d[0] == 1 and d[6:11] == [1, 1, 1, 0, 1]
    depthwise = (d[0] == 1 and d[5] == 1 and d[10] == co
                 and 1 <= d[6] <= 87 and d[7] in (1, 2)
                 and d[8] in (1, 2) and 0 <= d[9] <= 86)
    add = d[0] == 2 and d[3] == 1 and not any(d[5:11])
    ci = d[5] if pointwise else co
    tout = ((steps+2*d[9]-d[8]*(d[6]-1)-1)//d[7]+1
            if pointwise or depthwise else steps)
    if (not (pointwise or depthwise or add)
            or not 1 <= ci <= 1024 or not 1 <= co <= 1024
            or not 1 <= steps <= 800 or not 0 <= valid <= steps
            or not 1 <= tout <= 800
            or len(features) != ci*steps or not 0 <= tag <= 0xffffffff
            or (add and (not signed or branch1 is None or len(branch1) != ci*steps))
            or (not add and branch1 is not None)):
        raise ValueError('unsupported layer command')
    payload = b''.join(align16(a) for a in (arrays if add else arrays[1:]+arrays[:1]))
    record_header = [0x32524c51, 64+len(payload), 1, *d[:11], 0, 0]
    payload = (align16(features) + (align16(branch1) if add else b'')
               + struct.pack('<16I', *record_header) + payload)
    header = [0x32434e51, 1, 1, tag, len(payload), ci, steps, valid,
              co, int(signed), 2 if add else (1 if depthwise else 0),
              d[6] if depthwise else 0, d[7] if depthwise else 0,
              d[8] if depthwise else 0, d[9] if depthwise else 0, 0]
    return struct.pack('<16I', *header) + payload


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', default='data/export/quartznet_int8.bin')
    parser.add_argument('--output', default='data/export/quartznet_qnc2.bin')
    args = parser.parse_args()
    result = export(Path(args.input).read_bytes())
    Path(args.output).write_bytes(result)
    print(f'PASS: {len(result)} bytes exported')
