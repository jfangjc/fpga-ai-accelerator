"""Native callback conformance; set MOONSHINE_NATIVE_LIBRARY to the built .so.

These tests simulate exchanges and do not claim physical FPGA execution.
"""
import ctypes as ct
import errno
import os
from pathlib import Path
import struct
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'software'))
from moonshine.matrix import PackedWeights


@unittest.skipUnless(os.environ.get('MOONSHINE_NATIVE_LIBRARY'), 'Set MOONSHINE_NATIVE_LIBRARY for native callback tests')
class TransportTests(unittest.TestCase):
    def test_raw_accumulation_in_unpacker(self):
        lib = ct.CDLL(os.environ['MOONSHINE_NATIVE_LIBRARY'])
        callback_type = ct.CFUNCTYPE(ct.c_int, ct.c_void_p, ct.c_void_p, ct.c_void_p,
                                    ct.c_uint32, ct.c_uint32, ct.c_uint32, ct.c_int)
        errors = []
        resident = [None]
        def exchange(opaque, tx, rx, txbytes, rxbytes, count, batch):
            try:
                for p in range(count):
                    packet = ct.string_at(tx+p*txbytes, txbytes)
                    magic, op, k, tag = struct.unpack_from('<IIII', packet)
                    m = op >> 16
                    self.assertEqual(magic, 0x32434e51)
                    answer = struct.pack('<IIII', 0x32524e51, 0, k | m << 16, tag)
                    if op & 65535 == 1:
                        resident[0] = np.frombuffer(packet[16:], np.int8).reshape(-1, k, 16).transpose(0, 2, 1).reshape(-1, k)[:m].copy()
                    else:
                        self.assertEqual(op & 65535, 2)
                        x = np.frombuffer(packet[16:], np.int8).reshape(k, 16)
                        y = resident[0].astype(np.int32) @ x.astype(np.int32)
                        padded = np.zeros(((m+15)//16*16, 16), np.int32)
                        padded[:m] = y
                        answer += padded.reshape(-1, 16, 16).transpose(0, 2, 1).astype('<i4').tobytes()
                    self.assertEqual(len(answer), rxbytes)
                    ct.memmove(rx+p*rxbytes, answer, len(answer))
                return 0
            except Exception as error:
                errors.append(str(error))
                return -errno.EIO
        callback = callback_type(exchange)
        lib.qnc_create.argtypes = [callback_type, ct.c_void_p]
        lib.qnc_create.restype = ct.c_void_p
        lib.qnc_close.argtypes = [ct.c_void_p]
        lib.qnc_load_i8_single.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_uint, ct.c_uint]
        for name in ('qnc_run_i8', 'qnc_run_i8_accumulate'):
            getattr(lib, name).argtypes = [ct.c_void_p, ct.c_void_p, ct.c_size_t, ct.c_void_p]
        context = lib.qnc_create(callback, None)
        self.assertTrue(context)
        rng = np.random.default_rng(300)
        try:
            for m in (1, 3, 4, 17):
                weights = rng.integers(-127, 128, (m, 7), dtype=np.int8)
                self.assertEqual(lib.qnc_load_i8_single(context, weights.ctypes.data, m, 7), 0)
                for t in (1, 3, 4, 15, 16, 17, 33):
                    x = rng.integers(-127, 128, (7, t), dtype=np.int8)
                    expected = weights.astype(np.int32) @ x.astype(np.int32)
                    output = np.full((m, t), -1234567, np.int32)
                    self.assertEqual(lib.qnc_run_i8(context, x.ctypes.data, t, output.ctypes.data), 0)
                    np.testing.assert_array_equal(output, expected)
                    for initial in (0, 1234567, np.iinfo(np.int32).max, np.iinfo(np.int32).min):
                        output.fill(initial)
                        self.assertEqual(lib.qnc_run_i8_accumulate(context, x.ctypes.data, t, output.ctypes.data), 0)
                        np.testing.assert_array_equal(output, (expected.astype(np.int64)+initial).astype(np.int32))
            self.assertFalse(errors, errors)
        finally:
            lib.qnc_close(context)

    def test_borrowed_packet_is_restored_on_success_and_failures(self):
        lib = ct.CDLL(os.environ['MOONSHINE_NATIVE_LIBRARY'])
        callback_type = ct.CFUNCTYPE(ct.c_int, ct.c_void_p, ct.c_void_p, ct.c_void_p,
                                    ct.c_uint32, ct.c_uint32, ct.c_uint32, ct.c_int)
        mode, pointers, errors = ['ok'], [], []
        def exchange(opaque, tx, rx, txbytes, rxbytes, count, batch):
            try:
                pointers.append(tx)
                magic, opcode, k, tag = struct.unpack('<IIII', ct.string_at(tx, 16))
                self.assertEqual(magic, 0x32434e51)
                self.assertEqual(opcode & 0xffff, 1)
                self.assertEqual((count, batch, rxbytes), (1, 0, 16))
                if mode[0] == 'io':
                    return -errno.EIO
                answer = struct.pack('<IIII', 0 if mode[0] == 'ack' else 0x32524e51,
                                     0, k | (opcode & 0xffff0000), tag)
                ct.memmove(rx, answer, len(answer))
                return 0
            except Exception as error:
                errors.append(str(error))
                return -errno.EIO
        callback = callback_type(exchange)
        lib.qnc_create.argtypes = [callback_type, ct.c_void_p]
        lib.qnc_create.restype = ct.c_void_p
        lib.qnc_close.argtypes = [ct.c_void_p]
        lib.qnc_load_i8_single_packet.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_size_t, ct.c_uint, ct.c_uint]
        lib.qnc_load_i8_single.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_uint, ct.c_uint]
        lib.qnc_run_i8.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_size_t, ct.c_void_p]
        weights = np.arange(17*31, dtype=np.int16).astype(np.int8).reshape(17, 31)
        packet = PackedWeights(weights).packet
        payload = packet[16:].copy()
        context = lib.qnc_create(callback, None)
        self.assertTrue(context)
        try:
            self.assertEqual(lib.qnc_load_i8_single_packet(context, packet.ctypes.data, packet.size-1, 17, 31), -errno.EINVAL)
            self.assertFalse(pointers)
            for failure, result in (('ok', 0), ('io', -errno.EIO), ('ack', -errno.EPROTO)):
                mode[0] = failure
                self.assertEqual(lib.qnc_load_i8_single_packet(context, packet.ctypes.data, packet.size, 17, 31), result)
                self.assertEqual(pointers[-1], packet.ctypes.data)
                np.testing.assert_array_equal(packet[16:], payload)
                if result:
                    x, y = np.zeros((31, 1), np.int8), np.zeros((17, 1), np.int32)
                    self.assertEqual(lib.qnc_run_i8(context, x.ctypes.data, 1, y.ctypes.data), -errno.EINVAL)
                mode[0] = 'ok'
                self.assertEqual(lib.qnc_load_i8_single(context, weights.ctypes.data, 17, 31), 0)
                self.assertNotEqual(pointers[-1], packet.ctypes.data)
                np.testing.assert_array_equal(packet[16:], payload)
            self.assertFalse(errors, errors)
        finally:
            lib.qnc_close(context)


if __name__ == '__main__':
    unittest.main()
