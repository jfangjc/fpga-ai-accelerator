"""Native host transforms must exactly preserve the NumPy integer oracle."""
import ctypes as ct
import os
from pathlib import Path
import sys
import unittest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'software'))
from moonshine.matrix import NativeHost, Projection, quantize_input


@unittest.skipUnless(os.environ.get('MOONSHINE_NATIVE_LIBRARY'), 'Set MOONSHINE_NATIVE_LIBRARY')
class NativeHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host = NativeHost(ct.CDLL(os.environ['MOONSHINE_NATIVE_LIBRARY']))

    def test_quantization_layout_rounding_and_zero_scale(self):
        rng = np.random.default_rng(260)
        for rows, k in ((1, 1), (3, 7), (4, 4), (16, 320), (17, 1280), (33, 31)):
            for x in (rng.normal(size=(rows, k)).astype(np.float32), np.zeros((rows, k), np.float32)):
                expected, scale = quantize_input(x.T)
                actual, native_scale = self.host.quantize(x)
                np.testing.assert_array_equal(actual, expected)
                self.assertEqual(native_scale, scale)
        for peak in (np.float32(127), np.float32(127/1024), np.float32(127*1024)):
            x = np.resize(np.arange(-126.5, 127, .5, dtype=np.float32), 17*31).reshape(17, 31)
            x *= peak/127
            x[0, 0] = peak
            expected, scale = quantize_input(x.T)
            actual, native_scale = self.host.quantize(x)
            np.testing.assert_array_equal(actual, expected)
            self.assertEqual(native_scale, scale)

    def test_restoration_rounding_bias_and_transpose(self):
        rng = np.random.default_rng(41)
        for m, rows in ((1, 1), (7, 3), (4, 4), (320, 17), (1280, 16), (31, 33)):
            total = rng.integers(-2147483648, 2147483647, (m, rows), dtype=np.int32)
            scales = rng.uniform(.00001, .01, m).astype(np.float32)
            activation = np.float32(.013)
            for bias in (None, rng.normal(size=m).astype(np.float32)):
                expected = total.astype(np.float32)
                expected *= scales[:, None]*activation
                if bias is not None:
                    expected += bias[:, None]
                actual = self.host.restore(total, scales, activation, bias)
                np.testing.assert_array_equal(actual.view(np.uint32), expected.T.copy().view(np.uint32))

    def test_tiled_projection_keeps_one_global_input_scale(self):
        host = self.host
        class Device:
            def __init__(self): self.host = host
            def prepare(self, weights): return weights
            def multiply(self, weights, x): return weights.astype(np.int32) @ x.astype(np.int32)
        rng = np.random.default_rng(73)
        for m, k, rows, tile in ((37, 43, 5, 16), (1031, 1033, 5, 1024)):
            weights = rng.normal(size=(m, k)).astype(np.float32)
            bias = rng.normal(size=m).astype(np.float32)
            x = rng.normal(size=(1, rows, k)).astype(np.float32)
            expected = Projection(weights, bias, tile=tile)(x)
            actual = Projection(weights, bias, device=Device(), tile=tile)(x)
            np.testing.assert_array_equal(actual.view(np.uint32), expected.view(np.uint32))

    def test_invalid_and_nonfinite_inputs_are_rejected(self):
        for x in (np.empty((0, 3)), np.ones(4), np.array([[np.nan]]), np.array([[np.inf]])):
            with self.assertRaises(ValueError): self.host.quantize(x)
        for scales, activation, bias in (([0], 1, None), ([1], float('nan'), None), ([1], 1, [float('inf')])):
            with self.assertRaises(ValueError):
                self.host.restore(np.ones((1, 1), np.int32), scales, activation, bias)


if __name__ == '__main__':
    unittest.main()
