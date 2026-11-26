import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from CommunityStore import inventory


class NativeInventoryTests(unittest.TestCase):
    def test_distinct_registrations_of_same_files_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repo = root / "repo"
            (repo / ".git").mkdir(parents=True)
            addin = repo / "Ejix"
            addin.mkdir()
            (addin / "Ejix.manifest").write_text('{"type":"addin","id":"test","version":"1.1.0"}')
            (addin / "Ejix.py").touch()
            api = root / "Autodesk Fusion 360/API/AddIns"
            api.mkdir(parents=True)
            alias = api / "Ejix"
            try:
                alias.symlink_to(addin, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"Directory symlinks are unavailable: {error}")
            defaults = dict(name="Ejix", id="test", version="1.0.0", author="", description="",
                            isAddIn=True, isInternal=False, isVisible=True)
            direct = SimpleNamespace(**defaults, folder=str(addin), location=5, isRunning=True, isRunOnStartup=True)
            duplicate = SimpleNamespace(**defaults, folder=str(alias), location=2, isRunning=False, isRunOnStartup=False)
            internal = SimpleNamespace(**{**defaults, "isInternal": True}, folder=str(api), location=3)
            with patch.object(inventory, "locations", return_value=[root]):
                rows, native = inventory.from_fusion([direct, duplicate, internal])
                self.assertEqual(len(rows), 2)
                self.assertEqual(len(native), 2)
                direct_row = next(row for row in rows if row["folder"] == str(addin))
                alias_row = next(row for row in rows if row["folder"] == str(alias))
                self.assertTrue(direct_row["startup"])
                self.assertFalse(alias_row["startup"])
                self.assertTrue(direct_row["running"])
                self.assertFalse(alias_row["running"])
                self.assertEqual(direct_row["version"], "1.1.0")
                self.assertIsNone(inventory.archive_candidate(direct_row))
                self.assertEqual(inventory.archive_candidate(alias_row), alias)
                self.assertTrue(direct_row["checkout"])
                self.assertTrue(alias_row["checkout"])
                self.assertFalse(alias_row["writable"])
                self.assertEqual(direct_row["canonical"], alias_row["canonical"])
                self.assertEqual(inventory.conflict(rows, alias, None, exclude=alias_row["key"]), direct_row)
                by_id = inventory.conflict(rows, root / "AnotherFolder", "{TEST}", exclude=alias_row["key"])
                self.assertEqual(by_id, direct_row)
                self.assertIn(str(addin), inventory.duplicate_notice(by_id))
                self.assertIsNone(inventory.archive_candidate({**alias_row, "location": 0}))
                alias.unlink()
                alias.mkdir()
                self.assertIsNone(inventory.archive_candidate(alias_row))
                self.assertTrue((addin / "Ejix.py").is_file())


    def test_store_managed_installation_inside_its_checkout_can_be_updated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / ".git").mkdir()
            managed = root / "CommunityStore/AddIns"
            addon = managed / "Example"
            addon.mkdir(parents=True)
            (addon / "Example.manifest").write_text('{"type":"addin","version":"1.0.0"}')
            (addon / "Example.py").touch()
            script = SimpleNamespace(folder=str(addon), name="Example", id="example", version="1.0.0",
                                     author="", description="", location=5, isAddIn=True, isInternal=False,
                                     isVisible=True, isRunning=True, isRunOnStartup=True)
            rows, _ = inventory.from_fusion([script], managed)
            self.assertTrue(rows[0]["writable"])
            self.assertEqual(inventory.archive_candidate(rows[0], managed), addon)

    def test_removing_the_primary_registration_preserves_a_shared_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            managed = root / "AddIns"
            addon = managed / "Example"
            addon.mkdir(parents=True)
            (addon / "Example.manifest").write_text('{"type":"addin","id":"example"}')
            (addon / "Example.py").touch()
            alias = root / "Alias"
            try:
                alias.symlink_to(addon, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"Directory symlinks are unavailable: {error}")
            direct = SimpleNamespace(folder=str(addon), name="Example", id="example", version="",
                                     author="", description="", location=5, isAddIn=True, isInternal=False,
                                     isVisible=True, isRunning=False, isRunOnStartup=False)
            linked = SimpleNamespace(**{**vars(direct), "folder": str(alias)})
            rows, _ = inventory.from_fusion([direct, linked], managed)
            primary = next(row for row in rows if row["folder"] == str(addon))
            self.assertTrue(primary["writable"])
            self.assertIsNone(inventory.archive_candidate(primary, managed))
            self.assertTrue((alias / "Example.py").is_file())

    def test_external_addin_can_be_linked_without_making_its_folder_store_managed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            addon = root / "Work projects" / "Example"
            addon.mkdir(parents=True)
            (addon / "Example.manifest").write_text(
                '{"autodeskProduct":"Fusion","type":"addin","id":"{Example-ID}","supportedOS":"windows | mac"}',
                encoding="utf-8",
            )
            (addon / "Example.py").touch()
            identity = inventory.local_addin(addon)
            self.assertEqual(identity, "example-id")

            script = SimpleNamespace(folder=str(addon), name="Example", id=identity, version="1.0.0",
                                     author="", description="", location=inventory.LINKED_LOCATION,
                                     isAddIn=True, isInternal=False, isVisible=True, isRunning=False,
                                     isRunOnStartup=False)
            managed = root / "CommunityStore/AddIns"
            rows, native = inventory.from_fusion([script], managed)
            row, = rows
            self.assertIs(native[row["key"]], script)
            self.assertTrue(row["external"])
            self.assertFalse(row["writable"])
            self.assertIsNone(inventory.archive_candidate(row, managed))
            self.assertIs(inventory.conflict(rows, root / "Another copy", identity), row)

    def test_local_addin_and_inventory_use_the_current_platform_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            addon = Path(directory) / "Example"
            addon.mkdir()
            (addon / "Example.manifest").write_text('{"type":"addin","id":"example"}')
            (addon / "Example.dll").write_bytes(b"not a DLL")
            dylib = addon / "Example.dylib"
            dylib.touch()
            script = SimpleNamespace(folder=str(addon), name="Example", id="example", version="1.0.0",
                                     author="", description="", location=inventory.LINKED_LOCATION,
                                     isAddIn=True, isInternal=False, isVisible=True, isRunning=False,
                                     isRunOnStartup=False)
            with patch.object(inventory, "PLATFORM", "mac"):
                self.assertEqual(inventory.local_addin(addon), "example")
                rows, _ = inventory.from_fusion([script])
                self.assertEqual(Path(rows[0]["entry"]), dylib)
            with patch.object(inventory, "PLATFORM", "windows"):
                with self.assertRaises(ValueError):
                    inventory.local_addin(addon)
                entry = addon / "Example.ts"
                entry.touch()
                self.assertEqual(inventory.local_addin(addon), "example")
                rows, _ = inventory.from_fusion([script])
                self.assertEqual(Path(rows[0]["entry"]), entry)

    def test_windows_managed_folder_is_writable_when_manifest_stem_differs_only_in_case(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            addon = root / "EXAMPLE"
            addon.mkdir()
            (addon / "Example.manifest").write_text('{"type":"addin","id":"example"}')
            (addon / "Example.py").touch()
            script = SimpleNamespace(folder=str(addon), name="Example", id="example", version="1.0.0",
                                     author="", description="", location=inventory.LINKED_LOCATION,
                                     isAddIn=True, isInternal=False, isVisible=True, isRunning=False,
                                     isRunOnStartup=False)
            with patch.object(inventory, "PLATFORM", "windows"):
                rows, _ = inventory.from_fusion([script], root)
                self.assertTrue(rows[0]["writable"])
                self.assertEqual(inventory.archive_candidate(rows[0], root), addon)
            with patch.object(inventory, "PLATFORM", "mac"):
                rows, _ = inventory.from_fusion([script], root)
                self.assertFalse(rows[0]["writable"])


if __name__ == "__main__":
    unittest.main()
