"""Portable checks for asset integrity, input bounds and board setup behavior."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
sys.path.insert(0,str(ROOT/'software/mvp'))
from integrity import sha,verify_assets
from setup_board import install_apps
from transcribe import validate_samples
from frontend import ValidFrameFrontend
import numpy as np


class ProjectTests(unittest.TestCase):
    def temporary(self):
        path=ROOT/'build/tests'
        path.mkdir(parents=True,exist_ok=True)
        return tempfile.TemporaryDirectory(dir=path)

    def test_shipped_assets(self):
        self.assertGreater(len(verify_assets(source_only=True)['files_sha256']),20)

    def test_wav_bounds(self):
        for n in (320,8000,16000,40959):
            validate_samples(n)
        for n in (0,319,40960,160000):
            with self.assertRaises(ValueError): validate_samples(n)

    def test_frontend_shape_and_finite_values(self):
        # Synthetic constants exercise frontend dimensions without model assets.
        with self.temporary() as folder:
            np.savez(Path(folder)/'frontend.npz',window=np.hanning(400).astype(np.float32),
                     fb=np.random.default_rng(11).uniform(.01,1,(80,257)).astype(np.float32))
            frontend=ValidFrameFrontend(folder)
            for n in (320,8000,16000,40959):
                audio=np.random.default_rng(37).uniform(-.5,.5,n).astype(np.float32)
                features,length=frontend(audio)
                self.assertEqual(features.shape,(1,80,n//160+1))
                self.assertEqual(features.dtype,np.float32)
                self.assertEqual(int(length[0]),n//160+1)
                self.assertTrue(np.isfinite(features).all())

    def test_manifest_detects_tampering(self):
        with self.temporary() as folder:
            root=Path(folder);(root/'config').mkdir()
            (root/'asset').write_text('original')
            (root/'config/assets.json').write_text(json.dumps({'files_sha256':{'asset':sha(root/'asset')}}))
            verify_assets(root)
            (root/'asset').write_text('changed')
            with self.assertRaises(ValueError): verify_assets(root)

    def test_manifest_rejects_path_escape(self):
        with self.temporary() as folder:
            root=Path(folder);(root/'config').mkdir()
            (root/'config/assets.json').write_text(json.dumps({'files_sha256':{'../outside':'0'*64}}))
            with self.assertRaises(ValueError): verify_assets(root)

    def test_installer_does_not_overwrite_existing_app(self):
        with self.temporary() as folder:
            root=Path(folder);installed=root/'installed'
            for app in ('asr-v3-bulk','asr-v3-branch512-ab166'):
                source=root/'firmware'/app;source.mkdir(parents=True)
                for name in (app+'.bit.bin',app+'.dtbo','shell.json'):
                    (source/name).write_text('expected')
            install_apps(root,installed)
            install_apps(root,installed)
            target=installed/'asr-v3-bulk/shell.json'
            target.write_text('existing different app')
            with self.assertRaises(RuntimeError): install_apps(root,installed)
            self.assertEqual(target.read_text(),'existing different app')

    def test_policy_context_keys(self):
        policy=json.loads((ROOT/'config/policies.json').read_text())
        for row in policy['selected']:
            self.assertIn(format(row['context_seconds'],'g'),policy['cpu_policies'])

    def test_rebuild_sources_are_present(self):
        for name in ('rtl/experimental/qn_branch512_ab_axis.sv','scripts/project.tcl'):
            self.assertTrue((ROOT/name).is_file())
        dependencies=json.loads((ROOT/'config/dependencies.json').read_text())['files_sha256']
        for name in ('drivers/branch/qn_tile.c','drivers/bulk/qn_tile.c',
                     'hardware/reference/asr_v2_system/asr_v2_system.bd'):
            self.assertIn(name,dependencies)


if __name__=='__main__':
    unittest.main()
