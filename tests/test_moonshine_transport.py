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
