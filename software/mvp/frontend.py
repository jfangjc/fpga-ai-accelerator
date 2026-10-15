"""Pinned Citrinet frontend and WordPiece decoder."""
import wave
from pathlib import Path
import numpy as np

def read_audio(path):
    with wave.open(str(path),'rb') as wav:
        if wav.getframerate()!=16000 or wav.getnchannels()!=1 or wav.getcomptype()!='NONE':
            raise ValueError('Expected uncompressed 16 kHz mono PCM WAV; resample explicitly first')
        width=wav.getsampwidth(); raw=wav.readframes(wav.getnframes())
    if width==2: return np.frombuffer(raw,dtype='<i2').astype(np.float32)/32768
    if width==3:
        b=np.frombuffer(raw,dtype=np.uint8).reshape(-1,3).astype(np.int32)
        x=b[:,0]|(b[:,1]<<8)|(b[:,2]<<16)
        x=(x ^ 0x800000)-0x800000
        return x.astype(np.float32)/8388608
    if width==4: return np.frombuffer(raw,dtype='<i4').astype(np.float32)/2147483648
    raise ValueError('Expected PCM16, PCM24 or PCM32 WAV')

class Frontend:
    def __init__(self, model_dir):
        constants=np.load(Path(model_dir)/'frontend.npz')
        self.window=np.pad(constants['window'],(56,56))
        self.fb=constants['fb'].reshape(80,257)
    def __call__(self,audio):
        if len(audio)<320: raise ValueError('Audio is too short for reflected STFT')
        audio=np.asarray(audio,dtype=np.float32)
        emphasized=np.concatenate((audio[:1],audio[1:]-np.float32(.97)*audio[:-1]))
        frames=np.lib.stride_tricks.sliding_window_view(np.pad(emphasized,(256,256),mode='reflect'),512)[::160]
        spectrum=np.fft.rfft(frames*self.window,axis=-1).astype(np.complex64)
        power=(spectrum.real*spectrum.real+spectrum.imag*spectrum.imag).T
        features=np.log(self.fb @ power + np.float32(2**-24)).astype(np.float32)
        valid=len(audio)//160+1
        mean=features[:,:valid].mean(1,keepdims=True)
        std=features[:,:valid].std(1,ddof=1,keepdims=True)
        features=(features-mean)/(std+np.float32(1e-5))
        features[:,valid:]=0
        features=np.pad(features,((0,0),(0,(-features.shape[-1])%16)))
        return np.ascontiguousarray(features[None],dtype=np.float32), np.array([valid],dtype=np.int64)

def decode(log_probs, encoded_length, vocabulary):
    blank=len(vocabulary); previous=blank; tokens=[]
    for i in log_probs[:int(encoded_length)].argmax(-1):
        i=int(i)
        if i!=previous and i!=blank: tokens.append(vocabulary[i])
        previous=i
    tokens=[t for t in tokens if t not in {'[PAD]','[UNK]','[CLS]','[SEP]','[MASK]'}]
    text=' '.join(tokens).replace(' ##','').strip()
    # NeMo AutoTokenizer.ids_to_text calls BertTokenizer.convert_tokens_to_string
    # directly; it does not apply HuggingFace decode's punctuation cleanup.
    return text

class ValidFrameFrontend(Frontend):
    def __call__(self, audio):
        if len(audio) < 320:
            raise ValueError('Audio is too short for reflected STFT')
        audio = np.asarray(audio, dtype=np.float32)
        emphasized = np.concatenate((audio[:1], audio[1:] - np.float32(0.97) * audio[:-1]))
        frames = np.lib.stride_tricks.sliding_window_view(np.pad(emphasized, (256, 256), mode='reflect'), 512)[::160]
        spectrum = np.fft.rfft(frames * self.window, axis=-1).astype(np.complex64)
        power = (spectrum.real * spectrum.real + spectrum.imag * spectrum.imag).T
        features = np.log(self.fb @ power + np.float32(2 ** (-24))).astype(np.float32)
        valid = len(audio) // 160 + 1
        mean = features[:, :valid].mean(1, keepdims=True)
        std = features[:, :valid].std(1, ddof=1, keepdims=True)
        features = (features - mean) / (std + np.float32(1e-05))
        features[:, valid:] = 0
        return (np.ascontiguousarray(features[None], dtype=np.float32), np.array([valid], dtype=np.int64))
