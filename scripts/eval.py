# Board WER on 40 test-clean clips: python scripts/eval.py [--golden for the PC model]
import json
import pathlib
import pickle
import subprocess
import sys
import soundfile
from load import librispeech

CLIPS = pathlib.Path("build/eval")

def board(*args, sudo=False):
    command = ["powershell.exe", "-NoProfile", "-File", "scripts/board.ps1", *args] + ["-Sudo"] * sudo
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout

# Word-level edit distance
def distance(reference, hypothesis):
    row = list(range(len(hypothesis) + 1))
    for i, r in enumerate(reference, 1):
        previous, row[0] = row[0], i
        for j, h in enumerate(hypothesis, 1):
            previous, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, previous + (r != h))
    return row[-1]

librispeech("test-clean")
root = pathlib.Path("data/LibriSpeech/test-clean")
texts = {}
for path in root.rglob("*.trans.txt"):
    for line in path.read_text().splitlines():
        key, text = line.split(" ", 1)
        texts[key] = text.lower()
ids = pathlib.Path("scripts/testset.txt").read_text().split()

hypotheses = {}
if "--golden" in sys.argv:
    from frontend import features, valid_frames
    from golden_int8 import greedy, network, quantize
    model = pickle.loads(pathlib.Path("data/int8.pkl").read_bytes())
    for key in ids:
        audio, _ = soundfile.read(next(root.rglob(f"{key}.flac")), dtype="float32")
        x = quantize(features(audio), model["input_scale"])
        hypotheses[key] = greedy(network(x, model["layers"], valid_frames(audio)))
else:
    CLIPS.mkdir(parents=True, exist_ok=True)
    for key in ids:
        audio, rate = soundfile.read(next(root.rglob(f"{key}.flac")), dtype="int16")
        soundfile.write(CLIPS / f"{key}.wav", audio, rate, subtype="PCM_16")
    board("run", "rm -rf /home/ubuntu/stt-eval", sudo=True)
    board("run", "mkdir /home/ubuntu/stt-eval")
    board("put", str(CLIPS), "/home/ubuntu/stt-eval/")
    app = "/opt/stt-caption"
    sttcap = f"{app}/bin/sttcap {app}/share/quartznet_engine.bin {app}/share/running_prior.json"
    run = (f"systemctl stop stt-caption; {app}/bin/stt-caption prepare && cd /home/ubuntu/stt-eval/eval && "
           f"for k in {' '.join(ids)}; do echo \"#$k\"; {sttcap} --wav $k.wav | grep '\"final\":true'; done; true")
    for line in board("run", run, sudo=True).splitlines():
        if line.startswith("#"):
            key = line[1:]
            hypotheses[key] = ""
        elif line.startswith("{"):
            hypotheses[key] = (hypotheses[key] + " " + json.loads(line)["text"]).strip()

errors = words = 0
for key in ids:
    reference = texts[key].split()
    errors += distance(reference, hypotheses[key].split())
    words += len(reference)
    print(key, hypotheses[key])
print(f"WER {100 * errors / words:.2f} % ({errors}/{words} words)")
