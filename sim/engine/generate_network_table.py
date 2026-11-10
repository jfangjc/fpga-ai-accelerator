from pathlib import Path
import sys,struct
sys.path.insert(0,'model')
from qnc2_export import read_program
from engine_model import network_allocation
records=read_program(Path('data/export/quartznet_qnc2.bin').read_bytes())
plan=network_allocation(records,320)['rows']
p=Path('rtl/engine/stt_engine_pkg.sv');s=p.read_text(); s=s[:s.index('    // Fixed exported graph:')]+ 'endpackage\n' if '    // Fixed exported graph:' in s else s
f='''    // Fixed exported graph: exact descriptor validation makes bank retirement safe.
    function automatic logic [511:0] network_record(input logic [7:0] index);
        case (index)
'''
for i,(d,a) in enumerate(records,1):
 n=64+sum((len(z)+15)//16*16 for z in a)
 h=[0x32524c51,n,i,*d[:11],0,0]
 f+=f"            8'd{i}: return 512'h"+''.join(f'{v:08x}' for v in h[::-1])+";\n"
f+='            default: return 0;\n        endcase\n    endfunction\n'
f+='    function automatic logic [8:0] network_banks(input logic [7:0] index);\n        case (index)\n'
for r in plan:
 b=r['source_banks'];v=r['bank']*64+(b[-1] if len(b)==2 else 0)*8+b[0]
 f+=f"            8'd{r['id']}: return 9'd{v};\n"
f+='            default: return 0;\n        endcase\n    endfunction\n'
s=s.replace('endpackage',f+'endpackage');p.write_text(s, newline='\n')

