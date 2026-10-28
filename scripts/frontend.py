# Co-authored by AI
# Float log-mel front end, matching NVIDIA NeMo v1.0.0 for QuartzNet. Every 160
# samples (10 ms): pre-emphasis 0.97, a centred 512-point FFT of a 320-sample
# Hann window, 64 Slaney mel bands, log(mel + 2^-24)
import librosa
import numpy
import torch

def mel_power(audio):
    x = torch.from_numpy(numpy.asarray(audio, dtype=numpy.float32).copy())
    x = torch.cat((x[:1], x[1:] - .97 * x[:-1]))
    window = torch.hann_window(320, periodic=False)
    spectrum = torch.stft(x, 512, 160, 320, window, center=True, pad_mode="reflect", return_complex=True)
    bands = torch.from_numpy(librosa.filters.mel(sr=16000, n_fft=512, n_mels=64))
    return (bands @ spectrum.abs().square()).numpy()

def log_mel(mel):
    return numpy.log(numpy.asarray(mel, numpy.float32) + numpy.float32(2**-24))

# Network input: log mel normalised per band over the utterance, padded to a multiple of 16 frames
def features(audio):
    z = log_mel(mel_power(audio))
    f = ((z - z.mean(1, keepdims=True)) / (z.std(1, ddof=1, keepdims=True) + 1e-5)).astype(numpy.float32)
    return numpy.pad(f, ((0, 0), (0, -f.shape[1] % 16)))

def valid_frames(audio):
    return len(audio) // 160 + 1
