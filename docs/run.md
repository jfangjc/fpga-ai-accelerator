# Run

The installed appliance ([setup.md](setup.md)) captions in two ways: live from the microphone, and from recorded
clips for evaluation.

## With the microphone

```
sudo systemctl start stt-caption          # or install with -Enable to start at every boot
```

The service loads the `stt-kv260` overlay (only in place of the boot default, never another project's overlay)
and listens.

| State | What you see |
|---|---|
| Listening | the LED is off |
| Speaking | the LED is on; every 120 ms a preview shows the stable words and an amber tentative tail |
| Phrase ended | 0.7 s after speech stops, the phrase is decoded once more and shown as the final |

Pressing the button ends the session; a new one starts a second later.

**Captions on a PC.** `python scripts/captions.py BOARD_IP` shows the live captions: finished phrases scroll and
the phrase being spoken is redrawn. Each event is one JSON line on TCP port 8765:

```
{"t":2.91,"phrase":0,"final":false,"text":"it was a pain to see them","tentative":"and a sword like pain"}
{"t":7.71,"phrase":0,"final":true,"text":"it was a pain to see them and a sword like pain to see the signs ..."}
```

**On the monitor** the captions are drawn at the largest whole-number scale that fits, centred (scale 3 at 1080p).
Under them a status strip shows listening or speaking, the last decode's time on the engine, and the caption delay:
that decode time plus the audio still queued behind it. After 5 s of silence the screen clears, so the next phrase
starts on an empty screen; the session log and the TCP stream keep everything.

**Settings** are in `/etc/default/stt-caption`: the microphone gain, the TCP port, whether to draw captions on the
monitor, the status strip and the silence before clearing. Apply them with `sudo systemctl restart stt-caption`.

**Session logs.** Every session writes `/var/lib/stt-caption/sessions/<time>/session.jsonl` (all events) and
`stderr.txt`; `latest` points at the newest, and the newest 20 are kept. `sudo /opt/stt-caption/bin/stt-caption
status` shows the overlay, the service and the latest captions.

## Without the microphone, for evaluation

```
python scripts/eval.py               # the board, on the 40-clip LibriSpeech test-clean set
python scripts/eval.py --golden      # the INT8 model on the PC, for comparison
```

The clips are replayed through the board exactly as live speech would be: PL front end, speech detection, phrase
finals on the PL engine. The captioning service is stopped for the run. Word error rate on the 40 clips (848 words):

| | WER |
|---|---|
| Board | 5.42 % |
| INT8 model on the PC (whole utterances) | 4.60 % |

One clip on the board: `/opt/stt-caption/bin/sttcap /opt/stt-caption/share/quartznet_engine.bin
/opt/stt-caption/share/running_prior.json --wav clip.wav` (a 16 kHz mono 16-bit WAV file; add `--paced` to
replay it in real time). On the PC: `python scripts/run.py clip.wav`.
