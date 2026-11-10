# QuartzNet golden reference

Run every command from the repository root, on Windows or Linux. No NeMo
installation is needed. All weights, datasets and generated vectors live in
ignored `data/`; do not commit them. Only `download`, `calibrate`, `export`
and `qnc2_export` are needed to produce the release assets. The other commands
check that the results reproduce.

Create the environment with `py -3.14 -m venv .venv` on Windows or
`python3 -m venv .venv` on Linux. Then activate it: `.venv\Scripts\activate`
on Windows, `source .venv/bin/activate` on Linux. The commands below use
`python` for the activated environment.

```
pip install -r model/requirements.txt
python model/download.py --datasets
python model/verify.py
python model/eval_wer.py --select --out data/wer_float.json
python model/calibrate.py --count 100 --percentile 99.99
python model/eval_wer.py --int8 data/int8.pkl --out data/wer_int8.json
python model/eval_wer.py --int8 data/int8.pkl --mode running --out data/wer_running.json
python model/export.py --vectors
python model/qnc2_export.py      # PL engine program: data/export/quartznet_qnc2.bin
python model/stream_sim.py
python model/stream_sim.py --eval
python model/stream_sim.py --eval --window 3 --lookahead 0.4
```

`frontend.py` defines the float PL target `mel_power` and PS log/normalization.
`frontend_fixed.py` belongs to the concurrent frontend worker. `quartznet_float`
loads the trusted NVIDIA archive into an explicit convolution/add graph and
supports folded and unfolded execution. It masks padded time positions at
every convolution. `golden_int8.layer` and `network` expose integer reference
APIs. `calibrate` is the only network quantization stage using floats; integer
inference accepts already quantized features. Pickles are trusted local caches,
not an interchange format. QNW1 is the C/RTL interchange format.

The 40 test clips are chosen by increasing distance of duration from 7.675 s,
lexicographic id tie-break, at most one clip per speaker, until 40 speakers;
the saved list is sorted by utterance id. This aims at 307 s without selecting
by model accuracy and is not the proposal's unidentified original clip list.
Calibration uses the first 100 dev-clean utterances in lexicographic id order.
Duration/transcript manifests are cached under `data/*_manifest.json`; create
them only after dataset extraction finishes and delete them if replacing data.

Measured full-clip WER: float 4.3632%, INT8 utterance 4.5991%, INT8 running
4.8349%; see the Model section of `docs/results.md` for exact commands.
The streaming script segments phrases with 10ms RMS energy VAD (threshold
0.01, 700ms silence, 200ms lead-in); end of clip flushes the last phrase.
It stitches newly emitted CTC frames across previews and scores the last
preview transcript against the phrase reference. Running normalization carries
across each phrase; centered frames are deferred by 16ms. Default study is the
first three saved test clips; use `--clips 40` for the full evaluation subset.
No claim of full receptive-field validity is made for approximate previews.
The three-clip study measured 90.77% preview WER with 1s/no lookahead, and
32.31% with 3s/400ms lookahead; final WER was 4.62% in both. Preview quality
is unfinished. The 80.57s theoretical receptive field prevents an exact
full-context implementation at the proposed live latency.
