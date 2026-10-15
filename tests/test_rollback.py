"""Exercise rollback when inference or firmware activation fails."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import run_board


class RollbackTests(unittest.TestCase):
    def run_case(self,failure=None):
        directory=ROOT/'build/tests';directory.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as folder:
            root=Path(folder)
            before={'driver_sha256':'same','irq':{'40':{'requested':'0-3'},'41':{'requested':'0-3'}}}
            def command(args,**kwargs):
                if failure=='activation' and args[:2]==['xmutil','loadapp']:
                    raise subprocess.CalledProcessError(9,args)
                if failure=='inference' and any(str(v).endswith('transcribe.py') for v in args):
                    raise subprocess.CalledProcessError(7,args)
                return subprocess.CompletedProcess(args,0)
            # Fake sysfs writes without touching the host's /sys tree.
            original_write=Path.write_text
            def write(path,*args,**kwargs):
                if path.as_posix().startswith('/sys/'):
                    return len(args[0])
                return original_write(path,*args,**kwargs)
            with patch.object(run_board,'ROOT',root), \
                 patch.object(run_board.os,'geteuid',return_value=0,create=True), \
                 patch.object(run_board,'preflight'),patch.object(run_board,'state',return_value=before), \
                 patch.object(run_board,'driver',return_value=root/'module.ko'), \
                 patch.object(run_board.signal,'signal'),patch.object(run_board.subprocess,'run',side_effect=command), \
                 patch.object(run_board,'activate_bulk') as restore, \
                 patch.object(run_board,'restore_irqs') as irq, \
                 patch.object(Path,'write_text',write), \
                 patch.object(sys,'argv',['run_board.py','--','clip.wav']):
                if failure:
                    with self.assertRaises(subprocess.CalledProcessError): run_board.main()
                else:
                    run_board.main()
                restore.assert_called_once()
                irq.assert_called_once_with(before['irq'])
                self.assertEqual(len(list((root/'reports/local').glob('restored-*.json'))),1)

    def test_success_restores_bulk(self): self.run_case()
    def test_failed_inference_restores_bulk(self): self.run_case('inference')
    def test_failed_activation_restores_bulk(self): self.run_case('activation')


if __name__=='__main__': unittest.main()
