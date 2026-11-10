"""Deterministic tiled schedule estimate; not a substitute for RTL simulation.

All bandwidth is charged at 65% HP efficiency. Tile weights are fetched once,
then retained over all time tiles. No overlap is assumed between layers.
"""
import argparse
import json
import math
from pathlib import Path
from qnc2_export import read_qnw


def network_allocation(records, input_steps, valid=None):
    """Five-region liveness planner, separate from the RTL timing model.

    Each bank holds 1024 channels x twenty 16-frame words (320 steps).
    C1 alone may consume up to 640 input steps at 64 channels. Outputs
    allocate before sources retire: no unsafe in-place convolution/add.
    Last uses come from the complete program; hardware independently
    validates the fixed QuartzNet graph before using the generated routing table.
    """
    valid = input_steps if valid is None else valid
    if not 1 <= input_steps <= 640 or not 0 <= valid <= input_steps:
        raise ValueError('network input length limit')
    last = {0: 0}
    for i, (d, _) in enumerate(records, 1):
        for src in ([d[1], d[2]] if d[0] == 2 else [d[1]]):
            if not 0 <= src < i:
                raise ValueError('nonlinear source')
            last[src] = i
    shapes = {0: (64, input_steps, valid, True)}
    resident, rows, peak = {0: 0}, [], 1
    for i, (d, _) in enumerate(records, 1):
        op, src, src1, relu, co, ci, k, stride, dilation, pad, groups = d[:11]
        if src not in resident or (op == 2 and src1 not in resident):
            raise ValueError('source not resident')
        channels, tin, vin, signed = shapes[src]
        if op == 2:
            if shapes[src1][:3] != (channels, tin, vin) or channels != co or not signed or not shapes[src1][3]:
                raise ValueError('add shape/signedness mismatch')
            tout, vout = tin, vin
        elif op == 1 and ci*groups == channels:
            tout = (tin+2*pad-dilation*(k-1)-1)//stride+1
            vout = max(0, (vin+2*pad-dilation*(k-1)-1)//stride+1)
        else:
            raise ValueError('convolution channel mismatch')
        words = co*((tout+15)//16)
        if not 1 <= tout <= 320 or words > 20480:
            raise ValueError('network bank capacity')
        free = sorted(set(range(5))-set(resident.values()))
        if not free:
            raise ValueError('network bank liveness exceeds five banks')
        bank = free[0]
        row = dict(id=i, bank=bank, source_banks=[resident[s] for s in
                   ([src, src1] if op == 2 else [src])], channels=co,
                   steps=tout, valid=vout, signed=not bool(relu), words=words)
        resident[i] = bank
        shapes[i] = (co, tout, vout, not bool(relu))
        peak = max(peak, len(resident))
        for s in list(resident):
            if s != i and last.get(s, 0) <= i:
                del resident[s]
        rows.append(row)
    if not rows or rows[-1]['channels'] != 29 or not rows[-1]['signed']:
        raise ValueError('network must end in signed logits')
    return dict(rows=rows, peak_banks=peak, bank_words=20480,
                bank_bytes=327680, banks=5, estimated_uram=60,
                max_input_steps=640, max_output_steps=320)


def array_harness_cycles(ci, co, steps):
    """Exact unstalled cycles for the former sim/engine/tb_array_top harness, not a DMA engine.

    One bias-load cycle + ci accepted terms + one product drain per tile;
    one selected output row takes four clocks through the requant handshake.
    Random source gaps and sink stalls are additional measured cycles.
    """
    if not (1 <= ci <= 1024 and 1 <= co <= 1024 and 1 <= steps <= 800):
        raise ValueError('S1 size limit')
    return math.ceil(steps/16) * (math.ceil(co/32)*(ci+2) + co*4)


def s1_t150_cycles(ci, co):
    """Unstalled S1 response-header cycle counter for the exported T=150 set.

    Separate from the provisional whole-network estimator below. Includes
    activation realignment (10 words/channel), three parameter arrays,
    initial weight fill, serialized output tiles and real controller/drain/
    requantizer overhead. Excludes response DMA and source/sink stalls.
    This closed form covers exported reduction widths divisible by 16 and
    >=32; small synthetic reductions use the controller's 32-clock spacing.
    """
    if not (32 <= ci <= 1024 and ci % 16 == 0 and 1 <= co <= 1024
            and (co == 29 or co % 32 == 0)):
        raise ValueError('outside exported S1 T150 schedule domain')
    tiles = (co+31)//32
    trailing_rows = (co-1)%32+1
    return (ci*10 + 3*((co+3)//4) + 2*ci + tiles*10*ci
            + 71 + tiles - (32-trailing_rows))


def s2_overlap_estimate(records, steps=150, mhz=100):
    """Proposed 8-channel x16-frame DW unit; NOT an RTL cycle prediction.

    Eight independently addressed 128-bit activation banks feed a window
    cache: one word per channel lane per cycle. A 32-word window per lane
    covers ceil((15*2+86*2+1)/16)+1=14 words, including alignment.
    Weights supply eight bytes per tap cycle. One shared 128-bit write port
    and the existing 16 requantizers serialize eight output rows.

    The overlap bound pairs only adjacent DW->PW records with one consumer.
    It assumes tile streaming, independent activation read ports and enough
    intermediate storage; dependencies/startup, DMA and software cost extra.
    """
    if steps != 150 or mhz <= 0:
        raise ValueError('estimate currently supports T150 only')
    times = {0: steps*2}
    consumers = {}
    for d, _ in records:
        for src in ([d[1], d[2]] if d[0] == 2 else [d[1]]):
            consumers[src] = consumers.get(src, 0)+1
    rows = []
    for i, (d, _) in enumerate(records, 1):
        op, src, _, _, co, ci, k, stride, dilation, pad, groups = d[:11]
        tin = times[src]
        t = (tin+2*pad-dilation*(k-1)-1)//stride+1 if op == 1 else tin
        times[i] = t
        nt = math.ceil(t/16)
        if op == 2:
            kind = 'add'
            # Two passes through the shared 16-lane requantizer, plus drain.
            cycles = 2*co*nt+16
        elif groups == co and ci == 1:
            kind = 'depthwise'
            # Initial window fill charged in full; tap loop, MAC drain,
            # eight serialized row requants/writes per channel/frame tile.
            cycles = math.ceil(co/8)*(math.ceil(tin/16)+nt*(k+4+8))+8
        else:
            kind = 'pointwise'
            if t != 150 or k != 1:
                raise ValueError('outside measured pointwise schedule domain')
            cycles = s1_t150_cycles(ci, co)
        rows.append(dict(id=i, source=src, kind=kind, steps=t, cycles=cycles))
    serial = sum(r['cycles'] for r in rows)
    hidden = 0
    for a, b in zip(rows, rows[1:]):
        if (a['kind'] == 'depthwise' and b['kind'] == 'pointwise'
                and b['source'] == a['id'] and consumers[a['id']] == 1):
            hidden += min(a['cycles'], b['cycles'])
    return dict(rows=rows, serial_cycles=serial, overlap_bound_cycles=serial-hidden,
                serial_ms=serial/(mhz*1000),
                overlap_bound_ms=(serial-hidden)/(mhz*1000),
                dw_macs=128, dw_read_bytes_per_cycle=128,
                dw_window_bytes=8*32*16)


def predict(records, steps, mhz=175, efficiency=.65, lanes=32, frames=16, dw_lanes=64):
    times = {0: steps*2}
    rows = []
    for index, (d, arrays) in enumerate(records, 1):
        op, src, _, _, o, ci, k, stride, dilation, pad, groups = d[:11]
        tin = times[src]
        t = (tin+2*pad-dilation*(k-1)-1)//stride+1 if op == 1 else tin
        times[index] = t
        fetch = math.ceil(sum(map(len, arrays))/(16*efficiency))
        if op == 2:
            macs = 0
            compute = math.ceil(o*t/16)*2+8  # two branch requants
            refill = 0
        elif groups == o and ci == 1:
            macs = o*t*k
            compute = math.ceil(o/dw_lanes)*t*(k+4)
            refill = math.ceil((len(arrays[0])/math.ceil(o/32)+12*min(o,32))/(16*efficiency))
        else:
            macs = o*t*ci*k
            # Wavefront fill/drain and 16-lane serialized output conversion.
            compute = math.ceil(o/lanes)*math.ceil(t/frames)*(ci*k+lanes+frames-2+math.ceil(lanes*frames/16))
            refill = math.ceil((len(arrays[0])/math.ceil(o/32)+12*min(o,32))/(16*efficiency))
        # Ping-pong weight prefetch overlaps except initial tile.
        scheduled = max(compute, fetch)+refill
        # Activation bank traffic: 16 read and 16 write bytes/cycle.
        ports = math.ceil((o*t+tin*(ci*groups if op == 1 else o))/16)
        cycles = max(scheduled, ports)
        rows.append(dict(id=index, op=op, steps=t, macs=macs, compute=compute,
                         dma=fetch, ports=ports, cycles=cycles, ms=cycles/(mhz*1000)))
    # Initial input + final logits transport are not overlapped.
    boundary = math.ceil((64*steps*2+29*times[len(records)])/(16*efficiency))
    cycles = sum(r['cycles'] for r in rows)+boundary
    macs = sum(r['macs'] for r in rows)
    return dict(steps=steps, mhz=mhz, efficiency=efficiency, cycles=cycles,
                ms=cycles/(mhz*1000), gmac_s=macs*mhz/1000/cycles,
                macs=macs, rows=rows)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--weights', default='data/export/quartznet_int8.bin')
    p.add_argument('--mhz', type=float, default=175)
    p.add_argument('--output')
    a = p.parse_args()
    records = read_qnw(Path(a.weights).read_bytes())
    result = [predict(records, t, a.mhz) for t in (150, 800)]
    if a.output:
        Path(a.output).write_text(json.dumps(result, indent=2)+'\n')
    for r in result:
        print(f"T={r['steps']}: {r['cycles']} cycles, {r['ms']:.3f} ms, {r['gmac_s']:.3f} GMAC/s")


def depthwise_cycles(channels, steps, taps, stride=1, dilation=1, padding=0,
                     frame_lanes=16):
    """Sixteen-channel RTL preparation estimate; exported T150 tolerance 2%.

    Each group preloads sixteen complete channel rows and sixteen weights/tap
    through pipelined four-cycle RAM ports, then reuses them for every tile.
    Each frame tile primes a three-word BRAM window and one lookahead word
    in five clocks, then shifts one byte per startup clock to align the
    tile's first sample. Tap issue is one clock, followed by two pipeline
    clocks and sixteen rows through the shared requantizer. Includes unpack and command startup;
    excludes response transfer and source stalls.
    """
    if frame_lanes != 16:
        raise ValueError('parallel depthwise requires 16 frame lanes')
    tout = (steps+2*padding-dilation*(taps-1)-1)//stride+1
    nin = (steps+15)//16
    nout = (tout+15)//16
    align = (-padding) % 16  # frame-tile advances are multiples of 16
    return (channels*nin + 3*((channels+3)//4) + 32*((taps+15)//16)
            + ((channels+15)//16)*(16*nin+taps+10+nout*(taps+26+align))
            + (channels+31)//32 + 44)

def add_t150_cycles(channels):
    """Exact unstalled residual RUN_LAYER preparation cycles at T=150.

    Two unpack passes cost 20*C, four parameter arrays cost 4*ceil(C/4),
    and each of ten words/channel costs 43 clocks: read/wait/capture (6),
    32 pipelined (lane, branch) issues, 4 drain clocks for the five-stage
    convert/sum pipeline, and write (1). Header/parser/start/finish cost 26.
    Response DMA excluded. Manifest channels are multiples of 32; other sizes
    are unvalidated.
    """
    if not 32 <= channels <= 1024 or channels % 32:
        raise ValueError('outside residual T150 schedule domain')
    return 450*channels + 4*((channels+3)//4) + 30




def serialized_network_cycles(records, input_steps, valid=None, mhz=100):
    """Serialized RUN_NETWORK preparation estimate (no DMA/source gaps).

    Includes record checks, parameter reception and initial ping-pong fill.
    Uses the implemented sixteen-channel DW and shared residual pipeline.
    Report the simulator's counters for acceptance; this model is approximate.
    Input steps are pre-C1; the traditional T150 case uses input_steps=300.
    """
    allocation = network_allocation(records, input_steps, valid)
    shapes = {0: input_steps}
    rows = []
    for index, ((d, arrays), row) in enumerate(zip(records, allocation['rows']), 1):
        op, src, _, _, co, ci, taps, stride, dilation, pad, groups = d[:11]
        tin, tout = shapes[src], row['steps']
        ni, no = (tin+15)//16, (tout+15)//16
        nb, tiles = (co+3)//4, (co+31)//32
        if op == 2:
            cycles = 43*co*no + 4*nb + 26
        elif groups == co and ci == 1:
            cycles = (3*nb + 32*((taps+15)//16)
                      + ((co+15)//16)*(16*ni+taps+10+no*(taps+26+(-pad)%16))
                      + tiles + 40)
        else:
            last = (co-1)%32+1
            cycles = (3*nb + 2*ci + tiles*no*max(ci,32)
                      + 67+tiles-(32-last))
            cycles = max(cycles, sum((len(a)+15)//16 for a in arrays)+16)
        cycles += 4  # registered descriptor geometry at each record start
        rows.append(dict(id=index, cycles=cycles, input_steps=tin, steps=tout))
        shapes[index] = tout
    boundary = 8 + (64*input_steps+15)//16 + 1
    cycles = boundary + sum(row['cycles'] for row in rows)
    return dict(input_steps=input_steps, output_steps=rows[-1]['steps'],
                cycles=cycles, ms=cycles/(mhz*1000), rows=rows,
                boundary_cycles=boundary, approximate=True)

