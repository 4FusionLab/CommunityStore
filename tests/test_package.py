"""Verify deterministic version stamping and checksums offline."""

import hashlib
import json
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
import unittest
import zipfile

from scripts.build import build
from scripts.check_project import validate

ROOT = Path(__file__).resolve().parents[1]


class PackageTests(unittest.TestCase):
    def test_metadata_contract(self):
        product, initialized = validate(ROOT, allow_template=True)
        self.assertTrue(product)
        self.assertIsInstance(initialized, bool)

    def test_reproducible_zip_and_checksum(self):
        name = json.loads((ROOT / '.fusion-addin.json').read_text(encoding='utf-8'))['name']
        manifest_path = ROOT / name / (name + '.manifest')
        original = manifest_path.read_bytes()
        with TemporaryDirectory() as temp:
            archive, checksum = build(ROOT, '2.3.4', Path(temp), allow_template=True)
            first = archive.read_bytes()
            checksum_line = hashlib.sha256(first).hexdigest() + '  ' + archive.name + '\n'
            self.assertEqual(checksum.read_bytes(), checksum_line.encode('ascii'))
            with zipfile.ZipFile(archive) as bundle:
                inputs = [member for member in bundle.namelist() if member != name + '/LICENSE']
                self.assertEqual(inputs, sorted(inputs, key=lambda member: PurePosixPath(member).parts))
                inner = json.loads(bundle.read(name + '/' + name + '.manifest'))
                self.assertEqual(inner['version'], '2.3.4')
                self.assertEqual(inner['type'], 'addin')
                self.assertIn(name + '/' + name + '.py', bundle.namelist())
                self.assertFalse(any(entry.endswith('/trusted-stores.json') for entry in bundle.namelist()))
                self.assertEqual(bundle.read(name + '/LICENSE'), (ROOT / 'LICENSE').read_bytes())
                for notice in ('lucide.LICENSE', 'markdown-it.LICENSE'):
                    self.assertIn(name + '/ui/vendor/' + notice, bundle.namelist())
                for language in ('en', 'ru'):
                    self.assertIn(f'{name}/locales/{language}.json', bundle.namelist())
                self.assertFalse(any('__pycache__' in entry or '.pyc' in entry
                                     for entry in bundle.namelist()))
            build(ROOT, '2.3.4', Path(temp), allow_template=True)
            self.assertEqual(first, archive.read_bytes())
        self.assertEqual(original, manifest_path.read_bytes())

    def test_packaging_excludes_store_runtime_folders(self):
        from scripts.build import candidate_files

        with TemporaryDirectory() as temp:
            package = Path(temp)
            entry = package / 'CommunityStore.py'
            entry.touch()
            for directory in ('.vscode', 'AddIns', '.community-store-staging',
                              '.community-store-removed', '.community-store-updates'):
                folder = package / directory
                folder.mkdir()
                (folder / 'local.txt').touch()
            self.assertEqual(candidate_files(package), [entry])

    def test_requires_initialization_for_product_build(self):
        if not json.loads((ROOT / '.fusion-addin.json').read_text(encoding='utf-8'))['initialized']:
            with self.assertRaises(ValueError):
                with TemporaryDirectory() as temp:
                    build(ROOT, '0.1.0', Path(temp))


if __name__ == '__main__':
    unittest.main()
