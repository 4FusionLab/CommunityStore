import importlib
import json
import queue
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from CommunityStore import releases, versions


class BatchUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Only the event-handler base types are needed for these orchestration tests.
        adsk = ModuleType("adsk")
        adsk.core = ModuleType("adsk.core")
        for name in ("HTMLEventHandler", "CommandCreatedEventHandler", "CustomEventHandler"):
            setattr(adsk.core, name, type(name, (), {}))
        adsk.core.DialogResults = SimpleNamespace(DialogOK=1, DialogCancel=0)
        with patch.dict(sys.modules, {"adsk": adsk, "adsk.core": adsk.core}):
            cls.runtime = importlib.import_module("CommunityStore.runtime")
        sys.modules.pop("CommunityStore.runtime", None)

    def setUp(self):
        self.store = self.runtime.Store.__new__(self.runtime.Store)
        self.store.rows = {
            key: {"key": key, "name": key, "version": version,
                  "writable": not kwargs.get("checkout"), **kwargs}
            for key, version, kwargs in (
                ("one", "1.0.0", {}), ("two", "1.0.0", {}),
                ("git", "1.0.0", {"checkout": True}), ("pinned", "2.0.0", {}),
                ("rollback", "3.0.0", {}), ("no-package", "1.0.0", {}))
        }
        self.release = {"tag": "v2.0.0", "assets": [
            {"name": "universal.zip", "compatible": True},
            {"name": "desktop.zip", "compatible": True},
            {"name": "other.zip", "compatible": False},
        ]}
        self.source = {"repo": "Org/Repo"}
        self.store.marketplace = Mock(return_value=[])
        self.store.registration_rows = Mock(return_value=[])
        self.store.version_plan = self.version_plan
        self.store.refresh_inventory = Mock()
        self.store.refresh_trusted_sources = Mock()
        self.store.prefetch_installed_histories = Mock()
        self.store.closed = Event()
        self.store.github_session = None
        self.store.self_reload_pending = False
        self.store.batch = self.store.pending = None
        self.store.stage_package = self.stage

    def test_dismissing_guide_persists_once_without_github_or_inventory(self):
        store = self.store
        store.preferences = {"language": "ru", "owners": ["Example"]}
        with tempfile.TemporaryDirectory() as directory:
            store.data = Path(directory)
            store.dispatch("dismissGuide", {})
            expected = {"language": "ru", "owners": ["Example"], "guideSeen": True}
            self.assertEqual(store.read_data("settings.json", {}), expected)
            self.assertEqual(store.preferences, expected)
            with patch.object(store, "write_data") as write:
                store.dispatch("dismissGuide", {})
                write.assert_not_called()
        store.refresh_inventory.assert_not_called()

    def test_settings_export_respects_save_dialog_and_reports_outcome(self):
        dialog = Mock(filename="support.zip")
        self.store.ui = Mock()
        self.store.ui.createFileDialog.return_value = dialog
        self.store.preferences = {"language": "en"}
        with patch.object(self.runtime.diagnostics, "export") as export:
            dialog.showSave.return_value = 0
            self.store.dispatch("exportLogs", {})
            export.assert_not_called()
            self.assertEqual(self.store.notice, "")

            dialog.showSave.return_value = 1
            self.store.dispatch("exportLogs", {})
            export.assert_called_once_with(Path(dialog.filename))
            self.assertTrue(dialog.initialFilename.endswith(".zip"))
            self.assertEqual(self.store.notice.key, "runtime.logs_exported")

            export.side_effect = PermissionError
            self.store.dispatch("exportLogs", {})
            self.assertEqual(self.store.notice.key, "runtime.logs_export_failed")

    def version_plan(self, row, _catalog, _registered=None):
        release = {} if row["key"] == "no-package" else self.release
        target = "v2.0.0" if row["key"] in {"pinned", "rollback"} else "Latest"
        return versions.plan_row(row, self.source, release, target), self.source, release

    def stage(self, row, _source, release, item):
        self.store.pending = {"row": row, "item": item, "asset": release["asset"]["name"]}

    def preview(self, mode, keys=()):
        plan = self.store.batch_plan(mode, keys)
        self.store.preview = {"token": "test", "mode": mode, "keys": keys, "rows": plan}
        return {item["key"]: item for item in plan}

    def test_github_palette_sign_in_cancel_restore_and_sign_out_keep_local_sources(self):
        store = self.store
        store.github_client_id = "test_app"
        store.github_attempt = None
        store.github_code = store.github_error = ""
        store.preferences = {"owners": ["4FusionLab", "Example"]}
        store.queue_check = Mock()
        store.publish_state = Mock()
        store.results = queue.SimpleQueue()
        store.complete = lambda kind, payload: store.results.put((kind, payload))
        vault = Mock()
        bearer = uuid4().hex

        def authorize(client_id, cancelled, show_code):
            self.assertEqual(client_id, store.github_client_id)
            self.assertFalse(cancelled.is_set())
            show_code("ABCD-1234")
            return bearer

        def immediate_thread(*, target, daemon):
            return SimpleNamespace(start=target)

        with (patch.object(self.runtime.credentials, "open_store", return_value=vault),
              patch.object(self.runtime.github_auth, "authorize", side_effect=authorize),
              patch.object(self.runtime.releases, "authenticated_profile", return_value=None),
              patch.object(self.runtime.threading, "Thread", side_effect=immediate_thread)):
            store.dispatch("githubSignIn", {})
            store.dispatch("githubCancel", {})
            store.process_results()
            self.assertIsNone(store.github_session)
            vault.save.assert_not_called()

            store.dispatch("githubSignIn", {})
            store.process_results()
            vault.save.assert_called_once_with(bearer)
            self.assertTrue(store.github_session)
            self.assertEqual(store.github_code, "")
            store.dispatch("githubSignOut", {})
            self.assertIsNone(store.github_session)
            vault.delete.assert_called_once()

            vault.load.return_value = bearer
            store.restore_github_session()
            self.assertTrue(store.github_session)
            self.assertEqual(store.preferences, {"owners": ["4FusionLab", "Example"]})

    def test_copy_github_code_uses_the_live_device_code_only(self):
        store = self.store
        store.github_attempt = Event()
        store.github_code = "ABCD-1234"
        with (patch.object(self.runtime.sys, "platform", "darwin"),
              patch("CommunityStore.localization.system_language", return_value="en"),
              patch.object(self.runtime.subprocess, "run") as copy):
            store.dispatch("githubCopyCode", {"code": "NOT-THE-CODE"})
            copy.assert_called_once_with(["/usr/bin/pbcopy"], input=b"ABCD-1234", check=True, timeout=2,
                                         stdout=self.runtime.subprocess.DEVNULL,
                                         stderr=self.runtime.subprocess.DEVNULL)
            self.assertEqual(store.github_copy_feedback.key, "github.code_copied")
            store.dispatch("githubCancel", {})
            store.dispatch("githubCopyCode", {})
            copy.assert_called_once()
            self.assertEqual(store.github_copy_feedback, "")

    def test_windows_clipboard_uses_hidden_system_command_and_reports_failure(self):
        store = self.store
        store.github_attempt = Event()
        store.github_code = "ABCD-1234"
        system_root = Path("Windows fixture")
        with (patch.object(self.runtime.sys, "platform", "win32"),
              patch("CommunityStore.localization.system_language", return_value="en"),
              patch.dict(self.runtime.os.environ, {"SystemRoot": str(system_root)}),
              patch.object(self.runtime.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
              patch.object(self.runtime.subprocess, "run") as copy):
            store.dispatch("githubCopyCode", {"code": "NOT-THE-CODE"})
            copy.assert_called_once_with(
                [str(system_root / "System32/clip.exe")], input=b"ABCD-1234", check=True, timeout=2,
                stdout=self.runtime.subprocess.DEVNULL, stderr=self.runtime.subprocess.DEVNULL,
                creationflags=0x08000000,
            )
            self.assertEqual(store.github_copy_feedback.key, "github.code_copied")
            copy.side_effect = self.runtime.subprocess.TimeoutExpired("clip.exe", 2)
            store.dispatch("githubCopyCode", {})
            self.assertEqual(store.github_copy_feedback.key, "github.copy_failed")

    def test_windows_open_folder_uses_file_association_with_literal_path(self):
        folder = Path("Add-in folder (тест)")
        with (patch.object(self.runtime.sys, "platform", "win32"),
              patch.object(self.runtime.os, "startfile", create=True) as startfile):
            self.runtime.Store.open_folder(folder)
        startfile.assert_called_once_with(str(folder))

    def test_reload_waits_for_stop_before_restarting_a_running_addin(self):
        store = self.store
        row = store.rows["one"]
        row["running"] = row["startup"] = True
        store.operations = {}
        store.is_idle = Mock(return_value=True)
        store.lifecycle = Mock()
        store.retry_observation = Mock()

        store.dispatch("reload", {"key": row["key"]})
        store.lifecycle.assert_called_once_with(row, False)
        store.observe_operations()
        store.lifecycle.assert_called_once()

        row["running"] = False
        store.observe_operations()
        store.lifecycle.assert_called_with(row, True)
        self.assertIn(row["key"], store.operations)

        row["running"] = True
        store.observe_operations()
        self.assertEqual(store.operations, {})
        self.assertEqual(store.notice, "")
        self.assertTrue(row["startup"])

    def test_rate_limit_keeps_cached_versions_at_runtime_and_persists_deadline(self):
        store = self.store
        repo = self.source["repo"]
        cached = {**self.release, "history": [self.release]}
        store.latest = {repo: cached}
        store.discovered = {"Org": [self.source]}
        store.preferences = {"owners": ["Org"]}
        store.history_requested = {repo}
        store.last_check = 900
        store.check_again = store.checking = False
        store.update_disabled = store.update_ready = store.update_downloading = False
        store.update_notice = ""
        store.version = "1.0.0"
        store.rows = {}
        store.publish_state = Mock()
        store.results = queue.SimpleQueue()
        store.complete = lambda kind, payload: store.results.put((kind, payload))

        def throttle(*args, **kwargs):
            releases.restore_rate_limit(1100)
            raise releases.RateLimited

        def immediate_thread(*, target, daemon):
            return SimpleNamespace(start=target)

        with (tempfile.TemporaryDirectory() as directory,
              patch.object(releases, "_retry_at", 0),
              patch.object(releases.time, "time", return_value=1000) as clock,
              patch.object(releases, "public_repositories", return_value=[self.source]) as discovery,
              patch.object(releases, "latest", side_effect=throttle) as latest,
              patch.object(self.runtime.self_update, "discover", return_value=None) as self_update,
              patch.object(self.runtime, "threading", SimpleNamespace(Thread=immediate_thread))):
            store.root = store.data = Path(directory)
            store.check()
            store.process_results()
            store.check()
            store.queue_check()
            self_update.assert_called_once()
            discovery.assert_called_once()
            latest.assert_called_once()
            saved = json.loads((store.data / "releases.json").read_text())
            self.assertEqual(saved["entries"], {repo: cached})
            self.assertEqual(saved["retryAt"], 1100)
            self.assertEqual(saved["checked"], 900)
            self.assertEqual(store.history_requested, {repo})
            clock.return_value = 1100
            latest.side_effect = None
            latest.return_value = cached
            store.check()
            store.process_results()
            self.assertEqual(latest.call_count, 2)
            self.assertEqual(store.history_requested, set())
            self.assertEqual(store.last_check, 1100)

    def test_update_all_is_sequential_and_reports_failures_successes_and_skips(self):
        plan = self.preview("all")
        self.assertEqual([item["key"] for item in plan.values() if item["direction"]], ["one", "two"])
        for key in ("git", "pinned", "rollback", "no-package"):
            self.assertTrue(plan[key]["reason"])
        self.store.apply_batch({"token": "test", "assets": {"one": "universal.zip", "two": "desktop.zip"}})
        self.assertEqual(self.store.pending["row"]["key"], "one")
        self.store.operation_finished(False, "Check failed")
        self.assertEqual(self.store.pending["row"]["key"], "two")
        self.assertEqual(self.store.pending["asset"], "desktop.zip")
        self.store.operation_finished(True, "Installed")
        self.assertIsNone(self.store.batch)
        self.assertIsNone(self.store.pending)
        self.assertEqual({item["key"]: item["status"] for item in self.store.batch_results}, {
            "one": "failed", "two": "completed", "git": "skipped", "pinned": "skipped",
            "rollback": "skipped", "no-package": "skipped",
        })

    def test_saving_a_target_persists_it_without_staging_a_package(self):
        row = {**self.store.rows["one"], "id": "unique"}
        self.store.preferences = {"automatic": True}
        with tempfile.TemporaryDirectory() as folder:
            self.store.data = Path(folder)
            self.store.save_target(row, "v2.0.0")
            saved = self.store.read_data("settings.json", {})
            self.assertEqual(versions.target_for(saved["targets"], row), "v2.0.0")
            self.assertIsNone(self.store.pending)
            self.store.save_target(row, versions.LATEST)
            saved = self.store.read_data("settings.json", {})
            self.assertEqual(versions.target_for(saved["targets"], row), versions.LATEST)
        self.assertIsNone(self.store.pending)

    def test_update_preserves_user_files_and_restores_registration_on_failure(self):
        for managed, start_succeeds in ((False, True), (False, False), (True, True), (True, False)):
            with self.subTest(managed=managed, start_succeeds=start_succeeds), tempfile.TemporaryDirectory() as directory:
                store = self.runtime.Store.__new__(self.runtime.Store)
                root = Path(directory).resolve()
                store.root = root
                store.addin_root = root / "AddIns"
                store.data = root / "profile"
                store.preferences = {}
                store.batch = None
                store.operations = {}
                store.retry_observation = Mock()
                store.publish_state = Mock()
                store.prefetch_installed_histories = Mock()
                store.is_idle = Mock(return_value=True)
                old = (store.addin_root if managed else root / "repo") / "Probe"
                work = root / "staging"
                package = work / "Probe"
                if not managed:
                    (old.parent / ".git").mkdir(parents=True)
                for folder, version in ((old, "1.0.0"), (package, "2.0.0")):
                    folder.mkdir(parents=True)
                    (folder / "Probe.py").write_text(version)
                    (folder / "Probe.manifest").write_text(json.dumps(
                        {"type": "addin", "id": "probe", "version": version}))
                (old / "user-data.txt").write_text("user settings")

                class Scripts(list):
                    def itemByPath(self, folder):
                        return next((item for item in self if item.folder == folder), None)

                    def addExisting(self, folder):
                        entry = SimpleNamespace(folder=folder, name="Probe", id="probe", version="",
                                                author="", description="", location=5, isVisible=True,
                                                isAddIn=True, isInternal=False, isValid=True,
                                                isRunning=False, isRunOnStartup=False)

                        def run():
                            if not start_succeeds and (Path(folder) / "Probe.py").read_text() == "2.0.0":
                                return False
                            entry.isRunning = True
                            return True

                        def stop():
                            entry.isRunning = False
                            return True

                        def unlink():
                            self.remove(entry)
                            return True

                        entry.run, entry.stop, entry.unlink = run, stop, unlink
                        self.append(entry)
                        return entry

                store.app = SimpleNamespace(scripts=Scripts())
                previous = store.app.scripts.addExisting(str(old))
                previous.isRunOnStartup = previous.isRunning = True
                store.refresh_inventory()
                row = next(iter(store.rows.values()))
                store.pending = {"row": row, "item": None, "repository": "Owner/Probe", "progress": {},
                                 "package": (work, package, "Probe.py", {"id": "probe", "version": "2.0.0"})}
                store.apply_if_idle()
                for _ in range(4):
                    store.observe_operations()
                self.assertIsNone(store.pending)
                original = old
                if managed and start_succeeds:
                    original, = (old.parent / ".community-store-backups").iterdir()
                self.assertEqual((original / "Probe.py").read_text(), "1.0.0")
                self.assertEqual((original / "user-data.txt").read_text(), "user settings")
                if not managed:
                    self.assertTrue((old.parent / ".git").is_dir())
                installed, = store.rows.values()
                self.assertTrue(installed["running"])
                self.assertTrue(installed["startup"])
                if start_succeeds:
                    self.assertEqual(installed["sourceType"], "community_store")
                    self.assertEqual(installed["version"], "2.0.0")
                    self.assertEqual(store.repository_for(installed), "Owner/Probe")
                    if not managed:
                        self.assertIsNone(store.app.scripts.itemByPath(str(old)))
                else:
                    self.assertEqual(installed["sourceType"], "community_store" if managed else "folder")
                    self.assertEqual(installed["version"], "1.0.0")
                    if not managed:
                        self.assertFalse((store.addin_root / "Probe").exists())

    def test_removal_archives_managed_data_and_restores_it_if_unlink_fails(self):
        linked = self.runtime.inventory.LINKED_LOCATION
        locations = SimpleNamespace(LinkedScriptSourceLocation=linked)
        for managed, unlink_succeeds in ((True, True), (True, False), (False, True)):
            with (self.subTest(managed=managed, unlink_succeeds=unlink_succeeds),
                  tempfile.TemporaryDirectory() as directory,
                  patch.object(self.runtime.adsk.core, "ScriptSourceLocations", locations, create=True)):
                root = Path(directory).resolve()
                store = self.store
                store.addin_root = root / "AddIns"
                folder = (store.addin_root if managed else root / "External") / "Probe"
                folder.mkdir(parents=True)
                (folder / "Probe.manifest").write_text('{"type":"addin","id":"probe"}')
                content = b"user settings"
                (folder / "user-data.bin").write_bytes(content)
                script = SimpleNamespace(folder=str(folder), name="Probe", id="probe", version="1.0.0",
                                         author="", description="", location=linked, isAddIn=True, isInternal=False,
                                         isVisible=True, isRunning=False, isRunOnStartup=True,
                                         unlink=Mock(return_value=unlink_succeeds))
                rows, store.scripts_by_key = self.runtime.inventory.from_fusion([script], store.addin_root)
                store.operations = {}
                store.is_idle = Mock(return_value=True)
                if unlink_succeeds:
                    store.remove(rows[0])
                else:
                    with self.assertRaises(RuntimeError):
                        store.remove(rows[0])
                script.unlink.assert_called_once()
                archive = root / ".community-store-removed"
                if managed and unlink_succeeds:
                    self.assertFalse(folder.exists())
                    saved, = archive.iterdir()
                    self.assertEqual((saved / "user-data.bin").read_bytes(), content)
                else:
                    self.assertEqual((folder / "user-data.bin").read_bytes(), content)
                    self.assertEqual(list(archive.glob("*")), [])

    def test_selection_and_single_rollback_require_preview_and_valid_asset(self):
        self.assertEqual(list(self.preview("selected", ["two", "two"])), ["two"])
        with self.assertRaises(ValueError):
            self.store.apply_batch({"token": "test", "assets": {"two": "other.zip"}})
        self.assertIsNone(self.store.pending)
        self.assertEqual(self.preview("single", ["rollback"])["rollback"]["direction"], "downgrade")
        self.store.rows["rollback"]["version"] = "2.0.0"
        with self.assertRaises(ValueError):
            self.store.apply_batch({"token": "test", "assets": {"rollback": "desktop.zip"}})
        self.assertIsNone(self.store.pending)
