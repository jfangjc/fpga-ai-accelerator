"""Concatenate test clips with silence gaps for acoustic microphone tests.

    python tests/mic/mic_playlist.py OUT.wav GAP_S LEAD_S CLIP_ID... | --all
Writes OUT.wav (16 kHz mono PCM16) and OUT.json with each clip's reference
text and start/end offset (s) within the WAV.
"""
import json
import sys
import wave

out, gap, lead, *ids = sys.argv[1:]
clips = {c['id']: c for c in json.load(open('data/cpu/clips.json'))}
if ids == ['--all']:
    ids = list(clips)
entries, pos = [], 0
with wave.open(out, 'wb') as w:
    w.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
    w.writeframes(bytes(2*int(16000*float(lead)))); pos += int(16000*float(lead))
    for i in ids:
        with wave.open(f'data/cpu/{i}.wav', 'rb') as r:
            assert r.getparams()[:3] == (1, 2, 16000)
            n = r.getnframes(); w.writeframes(r.readframes(n))
        entries.append(dict(id=i, text=clips[i]['text'], start=pos/16000, end=(pos+n)/16000)); pos += n
        g = int(16000*float(gap)); w.writeframes(bytes(2*g)); pos += g
json.dump(dict(wav=out, seconds=pos/16000, clips=entries), open(out[:-4]+'.json', 'w', newline='\n'), indent=1)
print(f'{len(entries)} clips, {pos/16000:.1f} s')
