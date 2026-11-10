"""Adversarial exact-requantisation checks, Python standard library only; run on board."""
import struct,tempfile,pathlib,subprocess
O,I=29,64
def blob(bias,mult,shift,weight=0):
 arrays=[bytes([weight])*2048,struct.pack('<29i',*bias),struct.pack('<29i',*mult),struct.pack('<29i',*shift)]
 payload=bytearray();d=[1,0,0,0,O,I,1,1,1,0,1,0]+[0]*20
 for j,a in enumerate(arrays):
  while (192+len(payload))%64:payload.append(0)
  d[12+j*2]=192+len(payload);d[13+j*2]=len(a);payload.extend(a)
 h=struct.pack('<4s7If',b'QNW1',1,64,1,128,64,192+len(payload),0,.1).ljust(64,b'\0')
 return h+struct.pack('<32I',*d)+payload
bias=([-2147483648,2147483647,-1,1,0,3,-3]*5)[:29]
mult=[2147483647]*29
shift=([-30,-1,0,1,31,32,33,2147483647]*4)[:29]
with tempfile.TemporaryDirectory() as td:
 p=pathlib.Path(td);(p/'weights').write_bytes(blob(bias,mult,shift));(p/'input').write_bytes(bytes(64*17))
 out=subprocess.check_output(['./build/sw/qnrt/network_dump',str(p/'weights'),str(p/'input'),'17','17'])
 expected=bytearray()
 for a,m,s in zip(bias,mult,shift):
  n=31+s;v=a*m;q=0 if n>=64 else ((abs(v)+(1<<(n-1)))>>n)*(-1 if v<0 else 1)
  expected.extend(bytes([max(-128,min(127,q))&255])*17)
 assert out==expected
 print('PASS 493 adversarial requant outputs (int32 extrema, n=1,63,64, huge shifts, tails)')
 (p/'weights').write_bytes(blob([2147483647]*29,mult,shift,1))
 assert subprocess.run(['./build/sw/qnrt/load_test',str(p/'weights')]).returncode!=0
 print('PASS accumulator overflow rejection')
