"""Malformed QNW1 regression, execute on board with ./build/sw/qnrt/load_test built."""
import pathlib,subprocess,tempfile,struct
blob=pathlib.Path('quartznet_int8.bin').read_bytes()
def check(data,valid=False):
 with tempfile.NamedTemporaryFile() as f:
  f.write(data);f.flush();p=subprocess.run(['./build/sw/qnrt/load_test',f.name])
  assert (p.returncode==0)==valid
check(blob,True)
for offset,value in [(0,0),(4,2),(8,32),(12,0),(16,64),(20,128),(24,1),(28,1),(32,0),(36,1),(64,3),(68,999),(76,2),(108,1),(112,65),(116,0),(144,1)]:
 b=bytearray(blob);struct.pack_into('<I',b,offset,value);check(b)
for n in [0,63,64,127,len(blob)-1]:check(blob[:n])
print('PASS malformed blob regressions')
