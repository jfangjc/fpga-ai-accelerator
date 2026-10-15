"""Check cold registration and warm physical-call accounting."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'software/mvp'))
import runtime


class CounterTests(unittest.TestCase):
    def timer(self):
        timer=object.__new__(runtime.AudioTimer)
        timer.library=object()
        timer.frontend=lambda audio:(np.zeros((1,80,3),np.float32),np.array([3],np.int64))
        timer.vocabulary=['a','b']
        return timer

    def call(self,ioctls,cold):
        before={k:0 for k in ('ioctls','loads','runs','tx_bytes','rx_bytes','ioctl_seconds','physical_calls','oracle_calls')}
        after=before|{'ioctls':ioctls,'loads':256,'physical_calls':128}
        class Runner:
            def run(self,*args): return np.array([[[0,1,0]]],np.float32),np.array([1],np.int64)
        with patch.object(runtime,'stats',side_effect=[before,after]):
            return self.timer().call(Runner(),np.zeros(320,np.float32),cold=cold)

    def test_first_call_registers_and_executes(self): self.call(48,True)
    def test_warm_call_reuses_registration(self): self.call(24,False)
    def test_cold_call_rejects_missing_registration(self):
        with self.assertRaises(RuntimeError): self.call(24,True)
    def test_warm_call_rejects_extra_registration(self):
        with self.assertRaises(RuntimeError): self.call(48,False)


if __name__=='__main__': unittest.main()
