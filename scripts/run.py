# Transcribe a 16 kHz mono WAV file with the INT8 model: python scripts/run.py clip.wav
import pathlib
import pickle
import sys
import soundfile
from frontend import features, valid_frames
from golden_int8 import greedy, network, quantize

if len(sys.argv) != 2 or not sys.argv[1].endswith(".wav"):
    sys.exit("usage: python scripts/run.py clip.wav")

audio, rate = soundfile.read(sys.argv[1], dtype="float32")

if rate != 16000 or audio.ndim != 1:
    sys.exit("need a 16 kHz mono WAV file")

model = pickle.loads(pathlib.Path("data/int8.pkl").read_bytes())
x = quantize(features(audio), model["input_scale"])

print(greedy(network(x, model["layers"], valid_frames(audio))))
