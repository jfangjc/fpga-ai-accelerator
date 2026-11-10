"""Numerical folding and exact primitive checks; no dataset required."""
import numpy as np
import torch
from quartznet_float import QuartzNet
from golden_int8 import requant,conv_acc

if __name__=='__main__':
    torch.set_num_threads(4); torch.manual_seed(0)
    a=QuartzNet(); b=QuartzNet(folded=False)
    from frontend import mel_power, log_normalize
    import librosa
    fb=librosa.filters.mel(sr=16000,n_fft=512,n_mels=64,norm='slaney')
    np.testing.assert_allclose(fb,a.sd['preprocessor.featurizer.fb'].numpy()[0],atol=1e-7)
    np.testing.assert_allclose(torch.hann_window(320,periodic=False).numpy(),a.sd['preprocessor.featurizer.window'].numpy(),atol=1e-7)
    m=mel_power(np.random.default_rng(0).uniform(-1,1,16003).astype(np.float32))
    full,s=log_normalize(m,'running'); p,s0=log_normalize(m[:,:31],'running'); r,s0=log_normalize(m[:,31:],'running',s0)
    np.testing.assert_array_equal(full,np.concatenate((p,r),axis=1))
    print('checkpoint mel/window and running-state partition: PASS')
    x=torch.randn(1,64,112)
    ta,_=a(x,capture=True,length=101); tb,_=b(x,capture=True,length=101)
    worst=0
    for name in ta:
        err=float((ta[name]-tb[name]).abs().max()); worst=max(worst,err)
        torch.testing.assert_close(ta[name],tb[name],atol=3e-3,rtol=3e-3)
    print('folded/unfolded max absolute error:',worst)
    z=requant(np.array([[-3,-1,1,3]],np.int32),[1<<30],[0])
    np.testing.assert_array_equal(z,[[-2,-1,1,2]])
    np.testing.assert_array_equal(requant(np.array([[2147483647]],np.int32),[2147483647],[32]),[[0]])
    np.testing.assert_array_equal(requant(np.array([[-2147483648]],np.int32),[2147483647],[40]),[[0]])
    np.testing.assert_array_equal(conv_acc(np.array([[1,2,3]],np.int8),np.array([[[1,2,1]]],np.int8),[0],padding=1),[[4,8,8]])
    print('integer rounding and edge convolution: PASS')
