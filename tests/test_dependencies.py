"""Check dependency installation without network access or third-party fixtures."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from setup_dependencies import install_archive


class DependencyTests(unittest.TestCase):
    def fixture(self, root, name='models/test.bin'):
        archive=root/'bundle.zip'
        content=b'synthetic test asset'
        with zipfile.ZipFile(archive,'w') as output:
            output.writestr(name,content)
        (root/'config').mkdir()
        data={'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
              'files_sha256':{name:hashlib.sha256(content).hexdigest()}}
        (root/'config/dependencies.json').write_text(json.dumps(data))
        return archive

    def temporary(self):
        path=ROOT/'build/tests'
        path.mkdir(parents=True,exist_ok=True)
        return tempfile.TemporaryDirectory(dir=path)

    def test_install_and_repeat(self):
        with self.temporary() as folder:
            root=Path(folder);archive=self.fixture(root)
            install_archive(archive,root)
            install_archive(archive,root)
            self.assertEqual((root/'models/test.bin').read_bytes(),b'synthetic test asset')

    def test_changed_archive_is_rejected(self):
        with self.temporary() as folder:
            root=Path(folder);archive=self.fixture(root)
            archive.write_bytes(archive.read_bytes()+b'changed')
            with self.assertRaisesRegex(ValueError,'archive SHA256'):
                install_archive(archive,root)
            self.assertFalse((root/'models').exists())

    def test_changed_destination_is_preserved(self):
        with self.temporary() as folder:
            root=Path(folder);archive=self.fixture(root)
            (root/'models').mkdir();target=root/'models/test.bin';target.write_bytes(b'keep')
            with self.assertRaisesRegex(ValueError,'overwrite'):
                install_archive(archive,root)
            self.assertEqual(target.read_bytes(),b'keep')

    def test_parent_path_is_rejected(self):
        with self.temporary() as folder:
            root=Path(folder);archive=self.fixture(root,'../outside.bin')
            with self.assertRaisesRegex(ValueError,'Unsafe'):
                install_archive(archive,root)


if __name__=='__main__': unittest.main()
