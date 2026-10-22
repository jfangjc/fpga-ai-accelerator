"""Tiled signed INT8 projections using the existing QNC2 bulk DMA transport."""
import ctypes as ct
import errno
from pathlib import Path
import threading
import time

import numpy as np


def quantize_rows(weights):
    weights = np.asarray(weights, dtype=np.float32)
    if weights.ndim != 2 or min(weights.shape) == 0 or not np.isfinite(weights).all():
        raise ValueError('Expected a finite, nonempty [out,in] weight matrix')
    scales = np.max(np.abs(weights), axis=1).astype(np.float32) / np.float32(127)
    scales[scales == 0] = 1
    return np.ascontiguousarray(np.clip(np.rint(weights / scales[:, None]), -127, 127), dtype=np.int8), scales


def quantize_input(x):
    x = np.asarray(x, dtype=np.float32)
    if not np.isfinite(x).all():
        raise ValueError('Nonfinite projection input')
    scale = np.float32(np.max(np.abs(x), initial=0) / np.float32(127))
    if scale == 0:
        scale = np.float32(1)
    # Reuse the float scratch allocation across rounding and clipping. Division
    # stays float32, with the same nearest-even convention as the oracle.
    values = np.divide(x, scale)
    np.rint(values, out=values)
    np.clip(values, -127, 127, out=values)
    return np.ascontiguousarray(values, dtype=np.int8), scale


class Stats(ct.Structure):
    _fields_ = [(n, ct.c_uint64) for n in ('ioctls', 'loads', 'runs', 'tx_bytes', 'rx_bytes')] + [('ioctl_seconds', ct.c_double)]


class NativeProfile(ct.Structure):
    _fields_ = [(n, ct.c_double) for n in ('load_pack_seconds', 'scan_seconds', 'run_pack_seconds', 'unpack_seconds')]
    _fields_ += [(n, ct.c_uint64) for n in ('mac_issue_cycles', 'input_stall_cycles', 'output_stall_cycles', 'active_cycles')]


class PackedWeights:
    """Reusable bulk packet; only its header changes between synchronous loads."""
    def __init__(self, weights):
        weights = np.asarray(weights)
        if weights.dtype != np.int8 or weights.ndim != 2 or not all(0 < d <= 1024 for d in weights.shape):
            raise ValueError('Expected an INT8 bulk weight tile in [1,1024]')
        self.m, self.k = weights.shape
        groups = (self.m+15)//16
        padded = np.zeros((groups*16, self.k), dtype=np.int8)
        padded[:self.m] = weights
        self.packet = np.zeros(16+groups*self.k*16, dtype=np.uint8)
        self.packet[16:] = padded.reshape(groups, 16, self.k).transpose(0, 2, 1).reshape(-1).view(np.uint8)


class NativeHost:
    """Exact native host transforms; these helpers never access the FPGA."""
    def __init__(self, library):
        self.lib = library
        try:
            library.qnc_quantize_rows_f32.argtypes = [ct.c_void_p, ct.c_size_t, ct.c_uint, ct.c_void_p, ct.c_void_p]
            library.qnc_restore_rows_i32.argtypes = [ct.c_void_p, ct.c_size_t, ct.c_uint,
                ct.c_void_p, ct.c_float, ct.c_void_p, ct.c_void_p]
        except AttributeError as error:
            raise RuntimeError('Rebuild the native library for --native-projections') from error

    def quantize(self, x):
        x = np.ascontiguousarray(x, dtype=np.float32)
        if x.ndim != 2 or min(x.shape) == 0:
            raise ValueError('Expected nonempty time-major projection input')
        rows, k = x.shape
        output = np.empty((k, rows), dtype=np.int8)
        scale = ct.c_float()
        status = self.lib.qnc_quantize_rows_f32(x.ctypes.data, rows, k, output.ctypes.data, ct.byref(scale))
        if status:
            raise ValueError('Invalid native quantization input: '+str(status))
        return output, np.float32(scale.value)

    def restore(self, total, scales, activation_scale, bias=None):
        total = np.ascontiguousarray(total)
        scales = np.ascontiguousarray(scales, dtype=np.float32)
        if total.dtype != np.int32 or total.ndim != 2 or min(total.shape) == 0:
            raise ValueError('Expected nonempty channel-major INT32 accumulation')
        m, rows = total.shape
        if scales.shape != (m,):
            raise ValueError('Expected one weight scale per output channel')
        if bias is not None:
            bias = np.ascontiguousarray(bias, dtype=np.float32)
            if bias.shape != (m,):
                raise ValueError('Expected one bias per output channel')
        output = np.empty((rows, m), dtype=np.float32)
        status = self.lib.qnc_restore_rows_i32(total.ctypes.data, rows, m, scales.ctypes.data,
            float(activation_scale), None if bias is None else bias.ctypes.data, output.ctypes.data)
        if status:
            raise ValueError('Invalid native restoration input: '+str(status))
        return output


class BulkDevice:
    """One single-cache device, serialized across all projections. No fallback."""
    def __init__(self, library, path='/dev/qn-tile', native_projections=False):
        if Path('/sys/module/qn_tile/parameters/bulk_protocol').read_text().strip() != '1':
            raise RuntimeError('Moonshine requires the bulk image and bulk driver')
        self.lib = ct.CDLL(str(Path(library).resolve()), use_errno=True)
        self.host = NativeHost(self.lib) if native_projections else None
        self.lib.qnc_open.argtypes = [ct.c_char_p]
        self.lib.qnc_open.restype = ct.c_void_p
        self.lib.qnc_close.argtypes = [ct.c_void_p]
        self.lib.qnc_load_i8_single.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_uint, ct.c_uint]
        try:
            self.lib.qnc_load_i8_single_packet.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_size_t, ct.c_uint, ct.c_uint]
        except AttributeError as error:
            raise RuntimeError('Rebuild the Moonshine library with scripts/build_moonshine.sh') from error
        self.lib.qnc_run_i8.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_size_t, ct.c_void_p]
        self.lib.qnc_get_stats.argtypes = [ct.c_void_p, ct.POINTER(Stats)]
        self.lib.qnc_get_profile.argtypes = [ct.c_void_p, ct.POINTER(NativeProfile)]
        self.ctx = self.lib.qnc_open(path.encode())
        if not self.ctx:
            raise OSError(ct.get_errno(), 'Cannot open FPGA device')
        self.lock = threading.Lock()
        self.resident = None
        self.failed = False
        self.profile = {'load_seconds': 0., 'run_seconds': 0.}

    def close(self):
        if self.ctx:
            self.lib.qnc_close(self.ctx)
            self.ctx = None
        self.resident = None

    def stats(self):
        s = Stats()
        self.lib.qnc_get_stats(self.ctx, ct.byref(s))
        return {name: getattr(s, name) for name, _ in s._fields_}

    def timings(self):
        native = NativeProfile()
        self.lib.qnc_get_profile(self.ctx, ct.byref(native))
        return {**self.profile, 'native': {name: getattr(native, name) for name, _ in native._fields_}}

    def multiply(self, weights, x):
        prepared = isinstance(weights, PackedWeights)
        if not prepared:
            weights = np.ascontiguousarray(weights, dtype=np.int8)
        x = np.ascontiguousarray(x, dtype=np.int8)
        m, k = (weights.m, weights.k) if prepared else weights.shape
        if x.ndim != 2 or x.shape[0] != k or not 0 < m <= 1024 or not 0 < k <= 1024 or x.shape[1] == 0:
            raise ValueError('Bulk tile dimensions must be in [1,1024]')
        y = np.empty((m, x.shape[1]), dtype=np.int32)
        with self.lock:
            if self.failed or not self.ctx:
                raise RuntimeError('FPGA context is closed or failed')
            # Keep the resident array alive: an id alone can be recycled.
            if self.resident is not weights:
                start = time.perf_counter()
                if prepared:
                    rc = self.lib.qnc_load_i8_single_packet(self.ctx, weights.packet.ctypes.data, weights.packet.size, m, k)
                else:
                    rc = self.lib.qnc_load_i8_single(self.ctx, weights.ctypes.data, m, k)
                self.profile['load_seconds'] += time.perf_counter()-start
                if rc:
                    self.failed = True
                    raise OSError(-rc, 'FPGA weight load failed')
                self.resident = weights
            start = time.perf_counter()
            rc = self.lib.qnc_run_i8(self.ctx, x.ctypes.data, x.shape[1], y.ctypes.data)
            self.profile['run_seconds'] += time.perf_counter()-start
            if rc:
                self.failed = True
                raise OSError(-rc if rc < 0 else errno.EIO, 'FPGA multiply failed')
        return y

    def prepare(self, weights):
        return PackedWeights(weights)


class Projection:
    """Whole-matrix scales survive tiling; accumulation cannot overflow."""
    def __init__(self, weights, bias=None, device=None, tile=1024):
        if not 1 <= tile <= 1024:
            raise ValueError('Invalid tile size')
        self.weights, self.scales = quantize_rows(weights)
        self.m, self.k = self.weights.shape
        self.bias = None if bias is None else np.asarray(bias, dtype=np.float32).copy()
        if self.bias is not None and (self.bias.shape != (self.m,) or not np.isfinite(self.bias).all()):
            raise ValueError('Invalid bias')
        self.device = device
        self.tiles = [(o, i, np.ascontiguousarray(self.weights[o:o+tile, i:i+tile]))
                      for o in range(0, self.m, tile) for i in range(0, self.k, tile)]
        self.prepared = ([device.prepare(w) for _, _, w in self.tiles]
                         if device is not None and hasattr(device, 'prepare') else None)
        self.calls = 0
        self.profile = {'quantize_seconds': 0., 'multiply_seconds': 0., 'restore_seconds': 0.}

    def __call__(self, x):
        x = np.asarray(x, dtype=np.float32)
        if x.ndim < 1 or x.shape[-1] != self.k:
            raise ValueError('Projection input dimension mismatch')
        rows = int(np.prod(x.shape[:-1]))
        if rows == 0:
            return np.empty((*x.shape[:-1], self.m), dtype=np.float32)
        start = time.perf_counter()
        host = getattr(self.device, 'host', None)
        qx, sx = (host.quantize(x.reshape(rows, self.k)) if host else
                  quantize_input(x.reshape(rows, self.k).T))
        self.profile['quantize_seconds'] += time.perf_counter()-start
        start = time.perf_counter()
        # Every operand is bounded by 127, including all K tiles. Most model
        # projections fit INT32 exactly; retain INT64 for larger generic inputs.
        accumulator = np.int32 if self.k <= np.iinfo(np.int32).max // (127*127) else np.int64
        total = np.zeros((self.m, rows), dtype=accumulator)
        for index, (o, i, w) in enumerate(self.tiles):
            a = np.ascontiguousarray(qx[i:i+w.shape[1]])
            if self.device is not None:
                partial = self.device.multiply(self.prepared[index] if self.prepared is not None else w, a)
            else:
                partial = w.astype(np.int32) @ a.astype(np.int32)
            total[o:o+w.shape[0]] += partial
        self.calls += 1
        self.profile['multiply_seconds'] += time.perf_counter()-start
        start = time.perf_counter()
        if host and accumulator == np.int32:
            y = host.restore(total, self.scales, sx, self.bias).reshape(*x.shape[:-1], self.m)
            self.profile['restore_seconds'] += time.perf_counter()-start
            return y
        y = total.astype(np.float32)
        y *= self.scales[:, None] * sx
        if self.bias is not None:
            np.add(y, self.bias[:, None], out=y)
        y = y.T.reshape(*x.shape[:-1], self.m).copy()
        self.profile['restore_seconds'] += time.perf_counter()-start
        return y
