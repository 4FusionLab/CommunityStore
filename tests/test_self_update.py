"""Exercise the Fusion entry point and publisher with real versioned ZIPs."""
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from threading import Event
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

from CommunityStore import bootstrap, diagnostics, releases, self_update
from CommunityStore.localization import translate
from scripts.build import build

ROOT = Path(__file__).resolve().parents[1]
STARTUP = {"IsApplicationStartup": True}
MANUAL = {"IsApplicationStartup": False}
TEST_RUNTIME = """from . import config

def run(context, root, version, notice, disabled):
    root.joinpath("started.json").write_text(
        __import__("json").dumps([version, notice, disabled, config.ADDIN_NAME]), encoding="utf-8"
    )

def stop(context):
    pass
"""


class SelfUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve()
        # Exercise runtime activation without opening the user's OS credential store.
        self.enterContext(patch.dict(os.environ, {"COMMUNITY_STORE_GITHUB_CLIENT_ID": ""}))
        self.enterContext(patch.object(diagnostics.LOG, "handlers", []))
        self.enterContext(patch.object(diagnostics, "log_directory", return_value=self.folder / "logs"))
        self.addCleanup(self.close_logs)
        self.root = self.folder / "CommunityStore"
        self.directory = self.root / bootstrap.UPDATE_DIR
        self.cancelled = Event()
        archive, _ = build(ROOT, "1.0.0", self.folder)
        releases.extract(archive, self.folder)
        (self.root / "runtime.py").write_text(TEST_RUNTIME, encoding="utf-8")

    def close_logs(self):
        for handler in diagnostics.LOG.handlers:
            handler.close()

    def make_release(self, version, failure="", **metadata):
        archive, _ = build(ROOT, version, self.folder)
        output = io.BytesIO()
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(output, "w") as target:
            for item in source.infolist():
                data = source.read(item)
                if item.filename == "CommunityStore/runtime.py" and failure is not None:
                    data = (TEST_RUNTIME + failure).encode()
                target.writestr(item, data)
        payload = output.getvalue()
        url = f"https://github.com/{self_update.REPO}/releases/download/v{version}/{archive.name}"
        asset = {"name": archive.name, "browser_download_url": url,
                 "digest": "sha256:" + hashlib.sha256(payload).hexdigest()}
        response = io.BytesIO(payload)
        response.geturl = lambda: url
        release = {"tag_name": f"v{version}", "assets": [asset], **metadata}
        return release, response

    def stage(self, version, current="1.0.0", failure=""):
        release, response = self.make_release(version, failure)
        with (patch.object(releases, "github_json", return_value=release),
              patch.object(releases, "request", return_value=response)):
            candidate = self_update.discover(self.root, current, self.cancelled)
            return self_update.stage(self.root, candidate, self.cancelled)

    def run_entry(self, context):
        # A new interpreter models Fusion unloading the Python entry point and
        # its imports at exit. The SDK-bound runtime is a fixture in these tests.
        script = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from CommunityStore import CommunityStore, diagnostics
diagnostics.log_directory = lambda: Path(sys.argv[1]) / "logs"
assert not any(name.endswith(".runtime") for name in sys.modules)
context = {"IsApplicationStartup": sys.argv[2] == "True"}
CommunityStore.run(context)
CommunityStore.stop(context)
"""
        return subprocess.run([sys.executable, "-c", script,
                               str(self.folder), str(context["IsApplicationStartup"])],
                              capture_output=True, text=True, cwd=self.folder)

    def started(self, context=STARTUP):
        result = self.run_entry(context)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads((self.root / "started.json").read_text(encoding="utf-8"))

    def test_first_start_and_deferred_activation_preserve_installation(self):
        manifest = (self.root / "CommunityStore.manifest").read_bytes()
        entry = (self.root / "CommunityStore.py").read_bytes()
        addin = self.root / "AddIns" / "ManagedAddin" / "ManagedAddin.py"
        addin.parent.mkdir(parents=True)
        addin.write_text("managed", encoding="utf-8")
        self.assertEqual(self.started()[0], "1.0.0")
        self.assertFalse(self.directory.exists())
        self.assertEqual(translate(self.stage("1.1.0"), "en"), "Version 1.1.0 is ready. Restart Fusion.")
        self.assertEqual(self.started(MANUAL)[0], "1.0.0")
        self.assertEqual(self.started()[0], "1.1.0")
        self.assertEqual(self.started(MANUAL)[0], "1.1.0")
        self.assertEqual((self.root / "CommunityStore.manifest").read_bytes(), manifest)
        self.assertEqual((self.root / "CommunityStore.py").read_bytes(), entry)
        self.assertEqual(addin.read_text(encoding="utf-8"), "managed")

    def test_real_runtime_uses_the_same_fusion_profile_and_managed_folder(self):
        (self.root / "runtime.py").write_bytes((ROOT / "CommunityStore/runtime.py").read_bytes())
        profile = self.folder / "profile"
        settings = profile / "CommunityStore/settings.json"
        settings.parent.mkdir(parents=True)
        original_settings = '{"automatic": false, "interval": 120, "owners": ["Example"]}'
        settings.write_text(original_settings, encoding="utf-8")
        managed = self.root / "AddIns" / "Managed"
        managed.mkdir(parents=True)
        (managed / "Managed.manifest").write_text('{"type": "addin", "version": "2.0.0"}', encoding="utf-8")
        registration = SimpleNamespace(
            isAddIn=True, isVisible=True, isInternal=False, location=1,
            folder=str(managed), name="Managed", id="managed-id", version="2.0.0",
            author="", description="", isRunOnStartup=True, isRunning=True,
        )
        app = MagicMock()
        app.executeTextCommand.return_value = str(profile)
        store_registration = SimpleNamespace(**{
            **vars(registration), "folder": str(self.root), "name": "CommunityStore",
            "id": bootstrap.PRODUCT_ID, "version": "1.0.0",
        })
        app.scripts = [registration, store_registration]
        adsk, core = ModuleType("adsk"), ModuleType("adsk.core")
        core.Application = SimpleNamespace(get=lambda: app)
        core.HTMLEventHandler = core.CommandCreatedEventHandler = core.CustomEventHandler = object
        adsk.core = core
        with (patch.dict(sys.modules, {"adsk": adsk, "adsk.core": core}),
              patch("urllib.request.urlopen", side_effect=OSError("offline fixture"))):
            for version in ("1.0.0", "1.1.0"):
                with self.subTest(version=version):
                    runtime = bootstrap.launch(self.root, STARTUP)
                    try:
                        controller = runtime._controller
                        self.assertEqual(controller.version, version)
                        self.assertEqual(controller.addin_root, managed.parent)
                        self.assertEqual(controller.preferences["interval"], 120)
                        self.assertEqual(controller.preferences["owners"], ["4FusionLab", "Example"])
                        self.assertCountEqual(controller.scripts_by_key.values(), [registration, store_registration])
                        resource_path = app.userInterface.commandDefinitions.addButtonDefinition.call_args.args[-1]
                        self.assertTrue(Path(resource_path).is_dir())
                        self.assertEqual(Path(resource_path).parent, runtime.CODE_ROOT)
                        self.assertEqual(settings.read_text(encoding="utf-8"), original_settings)
                        if version == "1.0.0":
                            self.stage_from_installed_row(runtime, controller)
                    finally:
                        bootstrap.stop_runtime(runtime, {"IsApplicationClosing": True})
        self.assertEqual(app.unregisterCustomEvent.call_count, 2)
        self.assertTrue(registration.isRunOnStartup)

    def stage_from_installed_row(self, runtime, controller):
        release, response = self.make_release("1.1.0", failure=None)

        def immediate_thread(*, target, daemon):
            return SimpleNamespace(start=target)

        with (patch.object(runtime.threading, "Thread", side_effect=immediate_thread),
              patch.object(runtime.releases, "github_json", return_value=release),
              patch.object(runtime.releases, "public_repositories", return_value=[]),
              patch.object(runtime.releases, "request", return_value=response) as download):
            controller.dispatch("check", {})
            controller.process_results()
            download.assert_not_called()
            self.assertFalse(self.directory.exists())
            row = next(row for row in controller.snapshot()["installed"] if row.get("self"))
            self.assertTrue(row["canUpdate"])
            self.assertEqual(row["tag"], "v1.1.0")
            self.assertEqual(controller.snapshot()["updateCount"], 1)
            with self.assertRaises(ValueError):
                controller.dispatch("selfUpdate", {"key": row["key"], "tag": "v1.9.0"})
            download.assert_not_called()
            controller.dispatch("selfUpdate", {"key": row["key"], "tag": row["tag"]})
            controller.process_results()
            download.assert_called_once()
            state = controller.snapshot()
            self.assertEqual(state["storeVersion"], "1.0.0")
            self.assertEqual(state["updateCount"], 0)
            self.assertTrue(state["storeUpdateNotice"])

    def test_failed_update_restores_previous_confirmed_version(self):
        self.stage("1.1.0")
        self.assertEqual(self.started()[0], "1.1.0")
        self.stage("1.2.0", current="1.1.0",
                   failure='\ndef run(*args): raise RuntimeError("failed initialization")\n')
        version, notice, _, _ = self.started()
        self.assertEqual(version, "1.1.0")
        self.assertIn("1.2.0", notice)
        state = bootstrap.read_state(self.directory)
        self.assertEqual(state["rejected"], ["1.2.0"])
        self.assertIsNone(state["pending"])
        log = (self.folder / "logs" / diagnostics.LOG_NAME).read_text(encoding="utf-8")
        self.assertIn('loader.start_failed {"version":"1.2.0","error":"RuntimeError"', log)
        self.assertIn('loader.rollback {"version":"1.2.0"}', log)
        self.assertNotIn("failed initialization", log)
        with patch.object(releases, "github_json", return_value=self.make_release("1.2.0")[0]):
            with patch.object(releases, "request") as download:
                self.assertIsNone(self_update.discover(self.root, "1.1.0", self.cancelled))
                download.assert_not_called()
        self.stage("1.3.0", current="1.1.0")
        self.assertEqual(self.started()[0], "1.3.0")
        state = bootstrap.read_state(self.directory)
        (self.directory / state["active"] / "runtime.py").unlink()
        self.assertEqual(self.started()[0], "1.1.0")

    def test_process_exit_during_first_start_is_recovered(self):
        self.stage("1.1.0", failure="\nimport os\nos._exit(23)\n")
        self.assertEqual(self.run_entry(STARTUP).returncode, 23)
        interrupted = bootstrap.read_state(self.directory)
        self.assertEqual(interrupted["pending"], interrupted["trying"])
        version, notice, _, _ = self.started()
        self.assertEqual(version, "1.0.0")
        self.assertIn("1.1.0", notice)
        self.assertEqual(bootstrap.read_state(self.directory)["rejected"], ["1.1.0"])

    def test_interrupted_publish_leaves_previous_pending_package(self):
        self.stage("1.1.0")
        pending = bootstrap.read_state(self.directory)["pending"]
        previous_files = set(self.directory.iterdir())
        with patch.object(bootstrap, "save_state", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.stage("1.2.0")
        self.assertEqual(bootstrap.read_state(self.directory)["pending"], pending)
        self.assertEqual(set(self.directory.iterdir()), previous_files)
        self.assertEqual(self.started()[0], "1.1.0")

    def test_stage_ignores_a_release_no_newer_than_the_bundled_version(self):
        metadata, _ = self.make_release("1.0.0")
        release = {"tag": "v1.0.0", "version": "1.0.0", "asset": metadata["assets"][0]}
        with patch.object(releases, "request") as request:
            self.assertEqual(self_update.stage(self.root, release, self.cancelled), "")
            request.assert_not_called()
        self.assertFalse(self.directory.exists())

    def test_concurrent_publish_keeps_the_newer_pending_release(self):
        metadata, response = self.make_release("1.1.0")
        candidate = {"tag": "v1.1.0", "version": "1.1.0", "asset": metadata["assets"][0]}

        def publish_newer(phase, downloaded, total):
            if phase == "extracting":
                self.stage("1.2.0")

        with patch.object(releases, "request", return_value=response):
            notice = self_update.stage(self.root, candidate, self.cancelled, progress=publish_newer)
        state = bootstrap.read_state(self.directory)
        self.assertEqual(bootstrap.slot_version(state["pending"]), "1.2.0")
        self.assertEqual(translate(notice, "en"), "Version 1.2.0 is ready. Restart Fusion.")
        self.assertEqual([path.name for path in self.directory.iterdir() if path.is_dir()], [state["pending"]])

    def test_failed_activation_state_write_falls_back_even_if_cleanup_fails(self):
        self.stage("1.1.0")
        save_state = bootstrap.save_state
        stop_runtime = bootstrap.stop_runtime

        def fail_after_start(directory, state):
            if state.get("active"):
                raise OSError("state write failed")
            save_state(directory, state)

        def fail_after_stop(runtime, context):
            stop_runtime(runtime, context)
            raise RuntimeError("cleanup failed")

        with (patch.object(bootstrap, "save_state", side_effect=fail_after_start),
              patch.object(bootstrap, "stop_runtime", side_effect=fail_after_stop)):
            runtime = bootstrap.launch(self.root, STARTUP)
        self.addCleanup(bootstrap.stop_runtime, runtime, STARTUP)
        started = json.loads((self.root / "started.json").read_text(encoding="utf-8"))
        self.assertEqual(started[0], "1.0.0")
        state = bootstrap.read_state(self.directory)
        self.assertIsNone(state.get("trying"))
        self.assertNotIn("1.1.0", state.get("rejected", []))
        self.assertEqual(self.started()[0], "1.1.0")

    def test_windows_sharing_violation_during_start_preserves_pending_slot(self):
        self.stage("1.1.0")
        self.assertEqual(self.started()[0], "1.1.0")
        self.stage("1.2.0", current="1.1.0")
        original = bootstrap.read_state(self.directory)
        validate = bootstrap.validate_slot
        busy = PermissionError("Files are in use")
        busy.winerror = 32

        def validate_with_busy_slot(code, version):
            if version == "1.2.0":
                raise busy
            validate(code, version)

        with patch.object(bootstrap, "validate_slot", side_effect=validate_with_busy_slot):
            runtime = bootstrap.launch(self.root, STARTUP)
            self.addCleanup(bootstrap.stop_runtime, runtime, STARTUP)
        started = json.loads((self.root / "started.json").read_text(encoding="utf-8"))
        self.assertEqual(started[0], "1.1.0")
        self.assertEqual(bootstrap.read_state(self.directory), original)
        self.assertEqual(self.started()[0], "1.2.0")

    def test_state_replace_retries_a_windows_sharing_violation(self):
        self.directory.mkdir()
        original = {"rejected": ["1.0.0"]}
        bootstrap.save_state(self.directory, original)
        replace = os.replace
        busy = PermissionError("File is in use")
        busy.winerror = 32
        calls = 0

        def replace_after_reader_closes(source, target):
            nonlocal calls
            calls += 1
            if calls == 1:
                self.assertEqual(bootstrap.read_state(self.directory), original)
                raise busy
            return replace(source, target)

        updated = {"rejected": ["1.0.0", "1.1.0"]}
        with patch.object(os, "replace", side_effect=replace_after_reader_closes):
            bootstrap.save_state(self.directory, updated)
        self.assertEqual(calls, 2)
        self.assertEqual(bootstrap.read_state(self.directory), updated)
        self.assertEqual({path.name for path in self.directory.iterdir()}, {"state.json"})

    def test_update_state_directory_cannot_redirect_to_another_installation(self):
        external = self.folder / "other-updates"
        external.mkdir()
        try:
            self.directory.symlink_to(external, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"Directory symlinks are unavailable: {error.__class__.__name__}")
        with self.assertRaises(OSError):
            bootstrap.state_dir(self.root)
        self.assertEqual(self.started()[0], "1.0.0")
        self.assertEqual(list(external.iterdir()), [])

    @unittest.skipUnless(sys.platform == "win32", "Requires the Windows filesystem")
    def test_windows_state_replace_retries_until_reader_closes(self):
        self.directory.mkdir()
        bootstrap.save_state(self.directory, {})
        state_file = self.directory / "state.json"
        with state_file.open("rb") as reader:
            with patch.object(bootstrap.time, "sleep", side_effect=lambda _: reader.close()) as retry:
                bootstrap.save_state(self.directory, {"rejected": ["1.0.0"]})
            retry.assert_called()
        self.assertEqual(bootstrap.read_state(self.directory), {"rejected": ["1.0.0"]})

    @unittest.skipUnless(sys.platform == "win32", "Requires Windows byte-range locks")
    def test_windows_update_lock_is_exclusive_across_processes(self):
        script = (
            "import sys\n"
            "from pathlib import Path\n"
            "from CommunityStore.bootstrap import update_lock\n"
            "try:\n"
            " with update_lock(Path(sys.argv[1])): pass\n"
            "except OSError: sys.exit(23)\n"
        )
        command = [sys.executable, "-c", script, str(self.directory)]
        with bootstrap.update_lock(self.directory):
            self.assertEqual(subprocess.run(command, cwd=ROOT, timeout=10).returncode, 23)
        self.assertEqual(subprocess.run(command, cwd=ROOT, timeout=10).returncode, 0)

    def test_git_checkout_and_worktree_ignore_existing_updates(self):
        self.stage("1.1.0")
        (self.folder / ".git").write_text("gitdir: /some/worktree", encoding="utf-8")
        with patch.object(releases, "github_json") as github:
            self.assertIsNone(self_update.discover(self.root, "1.0.0", self.cancelled))
            github.assert_not_called()
        version, _, disabled, _ = self.started()
        self.assertEqual(version, "1.0.0")
        self.assertIn("Git", disabled)
        self.assertIn("pending", bootstrap.read_state(self.directory))

    def test_incompatible_metadata_never_stages(self):
        for options in ({"prerelease": True}, {"draft": True}, {"tag_name": "v0.9.0"},
                        {"tag_name": "v1.2.0-beta"}, {"assets": []}):
            with self.subTest(options=options):
                release, _ = self.make_release("1.2.0", **options)
                with (patch.object(releases, "github_json", return_value=release),
                      patch.object(releases, "request") as download):
                    if options == {"assets": []}:
                        with self.assertRaises(ValueError):
                            self_update.discover(self.root, "1.0.0", self.cancelled)
                    else:
                        self_update.discover(self.root, "1.0.0", self.cancelled)
                    download.assert_not_called()
                self.assertFalse(self.directory.exists())

    def test_bad_digest_is_rejected_without_queuing_activation(self):
        release, response = self.make_release("1.1.0")
        release["assets"][0]["digest"] = "sha256:" + "0" * 64
        with (patch.object(releases, "github_json", return_value=release),
              patch.object(releases, "request", return_value=response)):
            candidate = self_update.discover(self.root, "1.0.0", self.cancelled)
            with self.assertRaises(ValueError):
                self_update.stage(self.root, candidate, self.cancelled)
        self.assertEqual(bootstrap.read_state(self.directory), {})
        self.assertEqual(list(self.directory.iterdir()), [])
        self.assertEqual(self.started()[0], "1.0.0")

if __name__ == "__main__":
    unittest.main()
