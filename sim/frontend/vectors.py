import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import numpy as np
from model.frontend_fixed import mel_power_fixed
import soundfile as sf
out=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'build/sim/frontend'  # build directory
clips=sorted((out/'speech').glob('*.flac'))
if not clips:
    raise RuntimeError('No real speech: run extract_speech.py first')
x,sr=sf.read(clips[0],dtype='int16')
assert sr==16000
x=x[:320+160*199]
assert len(x)==32160, 'Speech clip too short for 200 frames'
y,_=mel_power_fixed(x)
p=out
(p/'samples.mem').write_text(''.join(f'{int(v)*4&0x3ffff:05x}\n' for v in x), newline='\n')
(p/'expected.mem').write_text(''.join(f'{int(v):016x}\n' for v in y.ravel()), newline='\n')
print('Generated',len(y),'frames')
