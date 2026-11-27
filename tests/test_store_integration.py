"""Run palette actions through the real Store, with fake Fusion and HTTP boundaries."""
import base64
import hashlib
import importlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from CommunityStore import credentials, diagnostics, github_auth, releases, trusted_stores


ADDINS = {
    "4FusionLab/ExporTo": "ExporTo",
    "ACDC4Robot/Fusion360": "ACDC4Robot",
    "906grown/gridfinity-fusion360": "gridfinity_v2",
}


class FusionScripts(list):
    def __init__(self, managed_root):
        super().__init__()
        self.managed_root = managed_root

    def itemByPath(self, folder):
        return next((script for script in self if os.path.normcase(script.folder) == os.path.normcase(folder)), None)

    def addExisting(self, folder):
        path = Path(folder)
        script = SimpleNamespace(
            folder=folder, name=path.name, isAddIn=True, isVisible=True, isInternal=False, isValid=True,
            isRunning=False, isRunOnStartup=False, id="", version="", author="", description="",
            location=2 if path.parent == self.managed_root else 5,
        )

        def set_running(state):
            script.isRunning = state
            return True

        def unlink():
            self.remove(script)
            return True

        script.run = lambda: set_running(True)
        script.stop = lambda: set_running(False)
        script.unlink = unlink
        self.append(script)
        return script


def addin_manifest(name, version):
    return {"type": "addin", "id": name + "-test-id", "version": version, "supportedOS": "windows|mac"}


class PublicGitHub:
    def __init__(self):
        self.addins = ADDINS.copy()
        self.versions = dict.fromkeys(ADDINS, "1.0.0")
        self.package_names = {}
        self.failures = {}
        self.requests = []

    def archive(self, repo, version):
        identity = self.addins[repo]
        name = self.package_names.get(repo, identity)
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr(zipfile.ZipInfo(f"{name}/{name}.manifest"),
                             json.dumps(addin_manifest(identity, version)))
            archive.writestr(zipfile.ZipInfo(f"{name}/{name}.py"), f"raise AssertionError('Fixture executed: {version}')")
        return data.getvalue()

    def response(self, request, timeout=12):
        url = request.full_url
        path = urlsplit(url).path
        bearer = request.get_header("Authorization")
        self.requests.append((path, bearer))
        failure = self.failures.get((path, bool(bearer)))
        if failure:
            raise failure
        parts = path.strip("/").split("/")
        if parts == ["user"]:
            data = {"login": "Fixture", "name": "Test account"}
        elif parts[:1] == ["users"]:
            data = [{"name": repo.split("/")[1], "full_name": repo}
                    for repo in self.addins if repo.split("/")[0].casefold() == parts[1].casefold()]
        else:
            api = parts[0] == "repos"
            repo = "/".join(parts[1:3] if api else parts[:2])
            version = self.versions[repo]
            name = self.addins[repo]
            if not api:
                response = io.BytesIO(self.archive(repo, parts[4].removeprefix("v")))
                response.headers = {}
                response.geturl = lambda: url
                return response
            if parts[3:] in (["releases", "latest"], ["releases"]):
                def release(package_version):
                    tag = "v" + package_version
                    filename = f"{self.package_names.get(repo, name)}-{tag}.zip"
                    return {"tag_name": tag, "assets": [{
                        "name": filename, "browser_download_url": f"https://github.com/{repo}/releases/download/{tag}/{filename}",
                        "digest": "sha256:" + hashlib.sha256(self.archive(repo, package_version)).hexdigest(),
                    }]}

                data = (release(version) if parts[-1] == "latest"
                        else [release(item) for item in dict.fromkeys((version, "1.0.0"))])
            elif parts[3:5] == ["git", "trees"]:
                data = {"tree": [{"path": f"{name}.{ext}", "sha": ext, "type": "blob"} for ext in ("py", "manifest")]}
            elif parts[3:5] == ["git", "blobs"]:
                encoded = base64.b64encode(json.dumps(addin_manifest(name, version)).encode()).decode()
                data = {"encoding": "base64", "content": encoded}
            else:
                raise AssertionError(path)
        response = io.BytesIO(json.dumps(data).encode())
        response.headers = {}
        return response


class StoreIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temporary).resolve()
        self.profile = self.root / "profile"
        self.data = self.profile / "CommunityStore"
        self.data.mkdir(parents=True)
        self.scripts = FusionScripts(self.root / "AddIns")
        self.palette = Mock(isValid=True, isVisible=False)
        self.ui = Mock(activeCommand="SelectCommand")
        self.ui.palettes.add.return_value = self.palette
        self.app = Mock(scripts=self.scripts, userInterface=self.ui, version="2.0.0")
        self.app.executeTextCommand.return_value = str(self.profile)
        adsk = ModuleType("adsk")
        adsk.core = ModuleType("adsk.core")
        for name in ("HTMLEventHandler", "CommandCreatedEventHandler", "CustomEventHandler"):
            setattr(adsk.core, name, type(name, (), {}))
        adsk.core.Application = SimpleNamespace(get=lambda: self.app)
        adsk.core.DialogResults = SimpleNamespace(DialogOK=1, DialogCancel=0)
        adsk.core.ScriptSourceLocations = SimpleNamespace(LinkedScriptSourceLocation=5)
        with patch.dict(sys.modules, {"adsk": adsk, "adsk.core": adsk.core}):
            self.runtime = importlib.import_module("CommunityStore.runtime")
        sys.modules.pop("CommunityStore.runtime", None)
        self.enterContext(patch.object(self.runtime, "adsk", adsk))
        self.native_addin_root = self.root / "Autodesk/Fusion/API/AddIns"
        self.enterContext(patch.object(self.runtime.inventory, "addin_roots", return_value=(self.native_addin_root,)))
        self.enterContext(patch.object(diagnostics, "start"))
        self.enterContext(patch.object(diagnostics, "event"))
        self.enterContext(patch.object(diagnostics, "failure"))
        self.enterContext(patch.object(releases, "_retry_at", 0))
        self.fetch_trusted = self.enterContext(patch.object(trusted_stores, "fetch", return_value=trusted_stores.FALLBACK_OWNERS))
        self.enterContext(patch.dict(self.runtime.os.environ, {"COMMUNITY_STORE_GITHUB_CLIENT_ID": "fixture_app"}))
        self.vault = Mock()
        self.vault.load.return_value = None
        self.enterContext(patch.object(credentials, "open_store", return_value=self.vault))
        self.github = PublicGitHub()
        self.enterContext(patch.object(releases, "urlopen", side_effect=self.github.response))
        self.enterContext(patch.object(releases, "_open_metadata", side_effect=self.github.response))
        threads = self.runtime.threading
        self.timers = []
        self.enterContext(patch.object(self.runtime, "threading", SimpleNamespace(
            Event=threads.Event, Thread=self.thread, Timer=self.timer,
        )))
        self.addCleanup(self.runtime.stop, None)

    def thread(self, *, target, daemon):
        return SimpleNamespace(start=target if target.__name__ != "schedule_checks" else lambda: None)

    def timer(self, delay, target, args):
        return SimpleNamespace(daemon=False, start=lambda: self.timers.append((target, args)))

    def start(self):
        self.runtime.run({"IsApplicationStartup": True}, self.root, "1.0.0", "", True)
        self.store = self.runtime._controller
        self.store.open_handler.notify(None)
        return self.send("state")

    def drain(self):
        for _ in range(25):
            self.store.completion.notify(None)
            if not self.timers:
                return
            (target, args) = self.timers.pop(0)
            target(*args)
        self.fail("Worker events and timers did not settle")

    def test_remote_default_sources_preserve_personal_subscriptions_and_last_valid_cache(self):
        (self.data / "settings.json").write_text(json.dumps({"owners": ["4fusionlab", "ACDC4Robot"]}))
        self.fetch_trusted.return_value = ("4FusionLab", "906grown", "ACDC4Robot")
        state = self.start()
        self.assertEqual(state["settings"]["trustedOwners"], ["4FusionLab"])
        self.drain()
        state = self.send("state")
        self.assertEqual(state["settings"]["owners"], ["4FusionLab", "906grown", "ACDC4Robot"])
        self.assertEqual(state["settings"]["trustedOwners"], state["settings"]["owners"])
        self.assertEqual({entry["repo"] for entry in state["catalog"]}, set(ADDINS))
        self.assertEqual(self.send("removeOwner", url="906GROWN")["settings"]["owners"],
                         state["settings"]["owners"])
        self.assertEqual(self.send("removeOwner", url="ACDC4Robot")["settings"]["ownerSubscriptions"], ["ACDC4Robot"])
        self.assertEqual(self.send("addOwner", url="https://github.com/CustomSource")["settings"]["owners"],
                         ["4FusionLab", "906grown", "ACDC4Robot", "CustomSource"])
        self.drain()

        self.runtime.stop(None)
        self.fetch_trusted.side_effect = ValueError("invalid remote configuration")
        self.assertEqual(self.start()["settings"]["trustedOwners"], ["4FusionLab", "906grown", "ACDC4Robot"])
        self.drain()
        self.assertEqual(self.send("state")["settings"]["trustedOwners"], ["4FusionLab", "906grown", "ACDC4Robot"])
        self.assertEqual(json.loads((self.data / trusted_stores.FILENAME).read_text())["owners"],
                         ["4FusionLab", "906grown", "ACDC4Robot"])

        self.fetch_trusted.side_effect = None
        self.fetch_trusted.return_value = ("4FusionLab",)
        self.send("preferences", automatic=False, interval=30)
        self.store.trusted_sources_retry = 0
        self.store.open_handler.notify(None)
        self.drain()
        state = self.send("state")
        self.assertEqual(state["settings"]["owners"], ["4FusionLab", "ACDC4Robot", "CustomSource"])
        self.assertEqual(state["settings"]["trustedOwners"], ["4FusionLab"])
        self.assertNotIn("906grown/gridfinity-fusion360", {entry["repo"] for entry in state["catalog"]})
        self.assertEqual(self.send("removeOwner", url="acdc4robot")["settings"]["owners"],
                         ["4FusionLab", "CustomSource"])

    def send(self, action, **data):
        event = SimpleNamespace(action=f"communityStore:{action}", data=json.dumps(data))
        self.store.receiver.notify(event)
        return json.loads(event.returnData)

    def check(self):
        self.send("check")
        self.drain()
        return self.send("state")

    def install(self, repo):
        self.assertTrue(self.send("install", repo=repo)["pending"])
        self.drain()
        return self.send("state")

    def test_removal_archive_survives_restart_unlink_and_errors(self):
        archive = self.root / ".community-store-removed"
        content = b"user settings"

        def register(parent, name, *, running=False):
            folder = parent / name
            folder.mkdir(parents=True)
            (folder / f"{name}.manifest").write_text(json.dumps(addin_manifest(name, "1.0.0")))
            (folder / "user-data.bin").write_bytes(content)
            script = self.scripts.addExisting(str(folder))
            script.isRunning = running
            row = next(item for item in self.send("state")["installed"] if item["name"] == name)
            return folder, script, row["key"]

        self.assertFalse(self.start()["canOpenRemovalArchive"])
        with patch.object(self.store, "open_folder") as open_folder:
            self.send("openRemovalArchive", folder=str(self.profile))
            open_folder.assert_not_called()

            for unlink_succeeds in (False, True):
                with self.subTest(unlink_succeeds=unlink_succeeds):
                    name = "Second" if unlink_succeeds else "First"
                    managed, _, key = register(self.scripts.managed_root, name, running=True)
                    pending = self.send("remove", key=key, confirmed=True)
                    self.assertEqual(pending["canOpenRemovalArchive"], unlink_succeeds)
                    self.drain()
                    state = self.send("state")
                    self.assertTrue(state["canOpenRemovalArchive"])
                    self.assertNotIn(str(archive), json.dumps(state))
                    self.assertFalse(managed.exists())
                    saved, = archive.glob(f"{name}-*")
                    self.assertEqual((saved / "user-data.bin").read_bytes(), content)
                    open_folder.assert_not_called()
                    self.send("openRemovalArchive", folder=str(self.profile), key=[])
                    open_folder.assert_called_once_with(archive)
                    open_folder.reset_mock()

                    external, script, key = register(self.root / "External", name)
                    if not unlink_succeeds:
                        script.unlink = Mock(return_value=False)
                    state = self.send("remove", key=key, confirmed=True)
                    self.assertTrue(state["canOpenRemovalArchive"])
                    self.assertEqual(bool(state["notice"]), not unlink_succeeds)
                    self.assertEqual(script in self.scripts, not unlink_succeeds)
                    self.assertEqual((external / "user-data.bin").read_bytes(), content)
                    self.assertEqual((saved / "user-data.bin").read_bytes(), content)
                    self.send("openRemovalArchive", folder=str(self.profile))
                    open_folder.assert_called_once_with(archive)
                    open_folder.reset_mock()

        self.runtime.stop(None)
        self.assertTrue(self.start()["canOpenRemovalArchive"])
        with patch.object(self.store, "open_folder") as open_folder:
            self.send("openRemovalArchive")
            open_folder.assert_called_once_with(archive)
        restored = self.root / "Recovered"
        restored.mkdir()
        for saved in archive.iterdir():
            moved = saved.rename(restored / saved.name)
            self.assertEqual((moved / "user-data.bin").read_bytes(), content)
        self.assertFalse(self.send("state")["canOpenRemovalArchive"])

    def test_archive_discovery_uses_validated_native_and_managed_roots(self):
        native_archive = self.native_addin_root.parent / ".community-store-removed"
        native_archive.mkdir(parents=True)
        (native_archive / "unrelated").mkdir()
        (native_archive / "File-deadbeef").touch()
        external = self.root / "Untrusted"
        (external / "Archived-deadbeef").mkdir(parents=True)
        self.assertFalse(self.start()["canOpenRemovalArchive"])
        self.store.preferences["removalArchive"] = str(external)
        managed_archive = self.root / ".community-store-removed"
        try:
            managed_archive.symlink_to(external, target_is_directory=True)
        except OSError:
            pass  # Directory symlinks may require additional privileges on Windows.
        with patch.object(self.store, "open_folder") as open_folder:
            self.assertFalse(self.send("openRemovalArchive", path=str(external))["canOpenRemovalArchive"])
            open_folder.assert_not_called()
            (native_archive / "Archived-deadbeef").mkdir()
            self.assertTrue(self.send("openRemovalArchive", path=str(external))["canOpenRemovalArchive"])
            open_folder.assert_called_once_with(native_archive)
            open_folder.reset_mock()

            if managed_archive.is_symlink():
                managed_archive.unlink()
            (managed_archive / "Newer-feedface").mkdir(parents=True)
            newer = native_archive.stat().st_mtime_ns + 2_000_000_000
            self.runtime.os.utime(managed_archive, ns=(newer, newer))
            self.send("openRemovalArchive")
            open_folder.assert_called_once_with(managed_archive)

    def test_palette_centers_and_fits_display_on_first_open(self):
        self.start()
        self.drain()
        self.send("state", display={"left": -900, "top": 24, "width": 900, "height": 700})
        self.palette.setPosition.assert_called_once_with(-876, 48)
        self.palette.setSize.assert_called_with(852, 652)
        self.store.open_handler.notify(None)
        self.send("state", display={"left": 0, "top": 0, "width": 1600, "height": 1000})
        self.palette.setPosition.assert_called_once()

    def test_guide_reset_persists_and_keeps_other_preferences(self):
        self.start()
        self.send("preferences", automatic=False, interval=60)
        self.assertTrue(self.send("dismissGuide")["settings"]["guideSeen"])
        reset = self.send("resetGuide")["settings"]
        saved = json.loads((self.data / "settings.json").read_text())
        for settings in (reset, saved):
            self.assertIs(settings["guideSeen"], False)
            self.assertIs(settings["automatic"], False)
            self.assertEqual(settings["interval"], 60)
        self.assertTrue(self.send("dismissGuide")["settings"]["guideSeen"])

    def test_profile_restores_unsubscribed_repository_after_confirmation_and_is_repeatable(self):
        self.start()
        self.drain()
        repo = "ACDC4Robot/Fusion360"
        self.github.versions[repo] = "2.0.0"
        doc = {"format": "fusion-community-store-profile", "schemaVersion": 1, "kind": "transfer",
               "name": "Workshop", "settings": {"automatic": False}, "ownerSubscriptions": ["CustomOwner"],
               "addins": [{"repo": repo, "manifestId": "acdc4robot-test-id", "installTarget": "v1.0.0",
                           "updateTarget": "Latest", "runOnStartup": False}]}
        path = self.root / "workshop.fusion-profile.json"
        picker = self.ui.createFileDialog.return_value
        picker.filename = str(path)
        picker.showOpen.return_value = picker.showSave.return_value = 1
        self.assertTrue(self.send("exportProfile", **doc)["profileSaved"])
        self.assertEqual(json.loads(path.read_text()), doc)
        self.send("importProfile")
        self.drain()
        before = self.send("state")
        plan = before["profilePreview"]
        self.assertEqual(plan["rows"][0]["action"], "install")
        self.assertTrue(before["settings"]["automatic"])
        self.assertFalse(before["installed"])
        self.assertEqual(plan["owners"], ["CustomOwner"])
        self.assertFalse(self.send("applyProfile", token="stale", selected={repo: None})["pending"])
        self.send("applyProfile", token=plan["token"], selected={repo: plan["rows"][0]["assets"][0]})
        self.drain()
        after = self.check()
        self.assertFalse(after["settings"]["automatic"])
        self.assertEqual(after["settings"]["ownerSubscriptions"], ["CustomOwner"])
        row, = after["installed"]
        self.assertEqual((row["installedTag"], row["version"], row["startup"], row["target"]),
                         ("v1.0.0", "1.0.0", False, "Latest"))
        self.assertEqual(row["repo"], repo)
        self.assertTrue(row["update"])
        self.send("importProfile")
        self.drain()
        self.assertEqual(self.send("state")["profilePreview"]["rows"][0]["action"], "skip")
        doc["addins"][0].update(runOnStartup=True, updateTarget="v1.0.0")
        path.write_text(json.dumps(doc))
        self.send("importProfile")
        self.drain()
        plan = self.send("state")["profilePreview"]
        self.assertEqual(plan["rows"][0]["action"], "configure")
        changed = self.send("applyProfile", token=plan["token"], selected={repo: ""})
        self.assertTrue(changed["installed"][0]["startup"])
        self.assertEqual(changed["installed"][0]["target"], "v1.0.0")

    def test_partial_collection_settings_preview_staleness_and_invalid_files(self):
        self.start()
        self.drain()
        path = self.root / "shared.json"
        picker = self.ui.createFileDialog.return_value
        picker.filename = str(path)
        picker.showOpen.return_value = 1
        doc = {"format": "fusion-community-store-profile", "schemaVersion": 1, "kind": "collection",
               "name": "Checks", "settings": {"interval": 120}}
        path.write_text(json.dumps(doc))
        plan = self.send("importProfile")["profilePreview"]
        self.assertEqual(plan["settings"], {"interval": {"from": 30, "to": 120}})
        self.send("preferences", automatic=False, interval=60)
        stale = self.send("applyProfile", token=plan["token"])
        self.assertEqual(stale["settings"]["interval"], 60)
        updated = self.send("applyProfile", token=stale["profilePreview"]["token"])
        self.assertEqual(updated["settings"]["interval"], 120)
        self.assertFalse(updated["settings"]["automatic"])
        for invalid in (dict(doc, schemaVersion=True), dict(doc, settings={"token": "unsupported"}),
                        dict(doc, settings={}), dict(doc, ownerSubscriptions=["../local"])):
            path.write_text(json.dumps(invalid))
            state = self.send("importProfile")
            self.assertIsNone(state["profilePreview"])
            self.assertTrue(state["notice"])

    def test_sources_packages_batch_and_local_registration(self):
        state = self.start()
        self.assertTrue(self.palette.isVisible)
        self.assertEqual(state["settings"]["owners"], ["4FusionLab"])
        self.assertEqual(state["settings"]["interval"], 30)
        self.assertEqual(self.send("preferences", automatic=False, interval=60)["settings"]["interval"], 60)
        rejected = self.send("preferences", automatic=False, interval=15.0)
        self.assertTrue(rejected["notice"])
        self.assertEqual(rejected["settings"]["interval"], 60)
        state = self.check()
        self.assertEqual([entry["repo"] for entry in state["catalog"]], ["4FusionLab/ExporTo"])
        self.send("addOwner", url="https://github.com/orgs/ACDC4Robot/repositories")
        self.send("addOwner", url="github.com/906grown/gridfinity-fusion360")
        self.drain()
        state = self.send("addOwner", url="906GROWN")
        self.drain()
        self.assertEqual(state["settings"]["owners"], ["4FusionLab", "ACDC4Robot", "906grown"])
        self.assertEqual({entry["repo"] for entry in self.send("state")["catalog"]}, set(ADDINS))
        self.assertEqual(self.send("removeOwner", url="4fusionlab")["settings"]["owners"][0], "4FusionLab")
        self.drain()

        for repo in ADDINS:
            state = self.install(repo)
            row = next(item for item in state["installed"] if item["releaseRepo"] == repo)
            self.assertEqual(row["version"], "1.0.0")
            self.assertTrue(row["running"])
            self.assertTrue(row["startup"])
        self.assertEqual(state["ownerCounts"], {owner.casefold(): 1 for owner in state["settings"]["owners"]})

        local = self.root / "Local"
        local.mkdir()
        (local / "Local.manifest").write_text(json.dumps(addin_manifest("Local", "1.0.0")))
        (local / "Local.py").write_text("raise AssertionError('Local fixture executed')")
        picker = self.ui.createFolderDialog.return_value
        picker.folder = str(local)
        picker.showDialog.return_value = 0
        self.assertEqual(len(self.send("registerLocal")["installed"]), 3)
        picker.showDialog.return_value = 1
        row = next(row for row in self.send("registerLocal")["installed"] if row["name"] == "Local")
        self.assertEqual(row["sourceType"], "folder")
        self.assertFalse(row["running"])
        self.assertTrue(self.send("registerLocal")["notice"])

        removed = "906grown/gridfinity-fusion360"
        state = self.send("removeOwner", url="https://github.com/906GROWN")
        self.drain()
        self.assertNotIn(removed, [entry["repo"] for entry in state["catalog"]])
        self.assertIn(removed, [row["releaseRepo"] for row in state["installed"]])
        self.assertTrue((self.root / "AddIns/gridfinity_v2/gridfinity_v2.py").is_file())

        self.github.versions.update(dict.fromkeys(ADDINS, "2.0.0"))
        self.assertEqual(self.check()["updateCount"], 2)
        preview = self.send("preview", mode="all")["preview"]
        self.assertEqual(sum(row["direction"] == "upgrade" for row in preview["rows"]), 2)
        self.assertTrue(self.send("applyBatch", token=preview["token"])["pending"])
        self.drain()
        state = self.send("state")
        versions = {row["name"]: row["version"] for row in state["installed"]}
        self.assertEqual(versions, {"ExporTo": "2.0.0", "ACDC4Robot": "2.0.0", "gridfinity_v2": "1.0.0", "Local": "1.0.0"})
        self.assertEqual(sorted(row["status"] for row in state["batchResults"]), ["completed", "completed", "skipped", "skipped"])
        self.assertTrue(all(row["running"] and row["startup"] for row in state["installed"] if row["name"] in ("ExporTo", "ACDC4Robot")))
        self.runtime.stop(None)
        state = self.start()
        self.assertEqual(state["settings"]["owners"], ["4FusionLab", "ACDC4Robot"])
        self.assertIn(removed, [row["releaseRepo"] for row in state["installed"]])

    def test_reports_open_only_the_selected_draft_or_security_policy_in_the_browser(self):
        self.start()
        self.check()
        repo = "4FusionLab/ExporTo"
        tag = "release/тест+1&body=override`@someone"
        self.store.latest[repo]["tag"] = tag
        self.send("language", language="ru")
        requests = list(self.github.requests)
        with patch.object(self.runtime.webbrowser, "open", return_value=True) as browser:
            draft_data = {"tag": "override", "folder": "/fixture/private", "log": "secret", "token": "private"}
            self.send("report", kind="addin", repo=repo, **draft_data)
            self.send("report", kind="addin", **draft_data)
            self.send("report", kind="source", owner="4FusionLab", repo=repo, **draft_data)
            self.send("report", kind="security", owner="4FusionLab", repo=repo, **draft_data)
            self.send("language", language="en")
            self.send("report", kind="source", owner="4FusionLab", **draft_data)
            blocked = self.send("report", kind="addin", repo="ThirdParty/Private")
            self.assertTrue(blocked["notice"])
            self.assertTrue(self.send("report", kind="source", owner="Unknown")["notice"])
            self.assertTrue(self.send("report", kind="other")["notice"])
        self.assertEqual(self.github.requests, requests)
        addin, local, source, private, english = [urlsplit(call.args[0]) for call in browser.call_args_list]
        for draft in (addin, local, source, english):
            self.assertEqual((draft.scheme, draft.netloc, draft.path),
                             ("https", "github.com", "/4FusionLab/CommunityStore/issues/new"))
            self.assertEqual(draft.fragment, "")
        self.assertEqual(parse_qs(addin.query), {"template": ["report-addin-ru.yml"],
                         "repository": [repo], "release-tag": [tag], "store-version": ["1.0.0"]})
        self.assertEqual(parse_qs(local.query), {"template": ["report-addin-ru.yml"], "store-version": ["1.0.0"]})
        self.assertEqual(parse_qs(source.query), {"template": ["report-source-ru.yml"],
                         "github-owner": ["4FusionLab"], "store-version": ["1.0.0"]})
        self.assertEqual(parse_qs(english.query), {"template": ["report-source.yml"],
                         "github-owner": ["4FusionLab"], "store-version": ["1.0.0"]})
        self.assertEqual(private.geturl(), "https://github.com/4FusionLab/CommunityStore/blob/main/SECURITY.ru.md")
        with patch.object(self.runtime.webbrowser, "open", return_value=False):
            self.assertTrue(self.send("report", kind="security")["notice"])

    def test_catalog_recovers_from_network_and_session_failures(self):
        self.start()
        path = "/users/4FusionLab/repos"
        repo = "4FusionLab/ExporTo"
        self.assertEqual(self.check()["catalog"][0]["repo"], repo)
        self.assertIn((path, None), self.github.requests)
        self.github.failures[(path, False)] = URLError("fixture offline")
        state = self.check()
        self.assertIn("4FusionLab", state["discoveryErrors"])
        self.assertEqual(state["catalog"][0]["repo"], repo)
        self.github.failures.clear()
        self.assertEqual(self.check()["discoveryErrors"], {})

        token = "fixture_token"
        with patch.object(github_auth, "authorize", return_value=token):
            self.send("githubSignIn")
            self.drain()
        state = self.send("state")
        self.assertTrue(state["github"]["connected"])
        self.assertEqual(state["github"]["profile"]["login"], "Fixture")
        self.assertNotIn(token, json.dumps(state))
        self.assertNotIn(token, (self.data / "settings.json").read_text() if (self.data / "settings.json").exists() else "")
        self.vault.save.assert_called_once_with(token)
        self.assertIn((path, "Bearer " + token), self.github.requests)
        self.install(repo)
        downloads = [(path, bearer) for path, bearer in self.github.requests if "/releases/download/" in path]
        self.assertTrue(downloads)
        self.assertTrue(all(bearer is None for _, bearer in downloads))

        self.github.failures[(path, True)] = HTTPError(f"https://api.github.com{path}", 401, "revoked", {}, None)
        before_check = len(self.github.requests)
        state = self.check()
        self.assertFalse(state["github"]["connected"])
        self.assertTrue(state["github"]["error"])
        self.vault.delete.assert_called_once()
        self.assertIn((path, None), self.github.requests[before_check:])
        self.assertEqual(state["catalog"][0]["repo"], repo)
        self.assertEqual(state["installed"][0]["releaseRepo"], repo)

    def test_installed_release_history_is_warmed_before_marketplace_and_survives_restart(self):
        repo = "4FusionLab/ExporTo"
        other = "4FusionLab/Another"
        self.github.versions[repo] = "2.0.0"
        self.github.addins = {other: "Another", **self.github.addins}
        self.github.versions[other] = "1.0.0"
        folder = self.root / "AddIns/ExporTo"
        folder.mkdir(parents=True)
        (folder / "ExporTo.manifest").write_text(json.dumps(addin_manifest("ExporTo", "1.0.0")))
        (folder / "ExporTo.py").write_text("")
        self.scripts.addExisting(str(folder))

        self.start()
        self.store.complete("requestCheck", None)
        self.drain()
        paths = [path for path, _ in self.github.requests]
        self.assertLess(paths.index(f"/repos/{repo}/releases"), paths.index(f"/repos/{other}/releases/latest"))
        self.assertNotIn(f"/repos/{other}/releases", paths)
        row = self.send("state")["installed"][0]
        self.assertTrue(row["historyLoaded"])
        self.assertEqual(row["targets"], ["v2.0.0", "v1.0.0"])

        self.runtime.stop(None)
        self.github.requests.clear()
        self.assertEqual(self.start()["installed"][0]["targets"], row["targets"])
        self.assertEqual(self.github.requests, [])
        self.send("target", key=row["key"], target="v1.0.0")
        self.assertEqual(self.send("state")["installed"][0]["target"], "v1.0.0")
        self.assertEqual(self.github.requests, [])

    def test_anonymous_catalog_advances_across_repeated_small_quota_windows(self):
        (self.data / "settings.json").write_text(json.dumps({"owners": ["ACDC4Robot", "906grown"]}))
        more_repositories = {f"4FusionLab/Probe{number}": f"Probe{number}" for number in range(5)}
        self.github.addins.update(more_repositories)
        self.github.versions.update(dict.fromkeys(more_repositories, "1.0.0"))
        self.start()
        now = 1000
        remaining = 3

        def rate_limited_response(request, timeout):
            nonlocal remaining
            self.assertGreater(remaining, 0)
            remaining -= 1
            response = self.github.response(request, timeout)
            if remaining == 0:
                response.headers.update({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(now + 60)})
            return response

        with (patch.object(releases.time, "time", side_effect=lambda: now),
              patch.object(releases, "urlopen", side_effect=rate_limited_response)):
            state = self.check()
            self.assertEqual(state["catalog"], [])
            self.assertGreater(state["retryAt"], now)
            self.assertEqual(len(self.github.requests), 3)
            for expected in range(1, len(self.github.addins) + 1):
                now += 61
                remaining = 3
                self.runtime.stop(None)
                self.start()
                state = self.check()
                self.assertEqual(len(state["catalog"]), expected)
            self.assertEqual({row["repo"] for row in state["catalog"]}, set(self.github.addins))
            self.assertEqual(len(self.github.requests), 3 * (len(self.github.addins) + 1))
            self.assertIsNone(self.store.catalog_scan)
            self.assertEqual(state["checked"], now)

    def test_anonymous_owner_resumes_second_page_and_replaces_completed_listing(self):
        owner = "ACDC4Robot"
        first = f"{owner}/Fusion360"
        second = f"{owner}/Second"
        overlap = f"{owner}/Overlap"
        removed = f"{owner}/Retired"
        extra = {removed: "Retired", second: "Second", overlap: "Overlap"}
        self.github.addins.update(extra)
        self.github.versions.update(dict.fromkeys(extra, "1.0.0"))
        (self.data / "settings.json").write_text(json.dumps({"owners": [owner]}))
        self.start()
        self.assertIn(removed, {row["repo"] for row in self.check()["catalog"]})

        now = 1000
        remaining = 2
        owner_pages = []

        def paginated_response(request, timeout):
            nonlocal remaining
            url = urlsplit(request.full_url)
            custom_owner = url.path == f"/users/{owner}/repos"
            if custom_owner:
                page = int(parse_qs(url.query)["page"][0])
                owner_pages.append(page)
            if remaining <= 0:
                raise HTTPError(request.full_url, 429, "Rate limited", {"Retry-After": "60"}, None)
            remaining -= 1
            if not custom_owner:
                return self.github.response(request, timeout)
            repos = [first, overlap] if page == 1 else [overlap, second]
            data = [{"name": repo.split("/")[1], "full_name": repo} for repo in repos]
            response = io.BytesIO(json.dumps(data).encode())
            response.headers = {}
            if page == 1:
                response.headers["Link"] = '<https://api.github.com/users/ACDC4Robot/repos?page=2>; rel="next"'
            return response

        with (patch.object(releases.time, "time", side_effect=lambda: now),
              patch.object(releases, "urlopen", side_effect=paginated_response)):
            state = self.check()
            self.assertEqual(owner_pages, [1, 2])
            self.assertIn(removed, {row["repo"] for row in state["catalog"]})
            saved = json.loads((self.data / "releases.json").read_text())
            page, sources = saved["anonymousPages"][owner]
            self.assertEqual(page, 2)
            self.assertEqual([source["repo"] for source in sources], [first, overlap])

            now += 62
            remaining = 10
            self.runtime.stop(None)
            self.start()
            state = self.check()
            self.assertEqual(owner_pages, [1, 2, 2])
            self.assertEqual(sorted(row["repo"] for row in state["catalog"]),
                             sorted(["4FusionLab/ExporTo", first, overlap, second]))
            saved = json.loads((self.data / "releases.json").read_text())
            self.assertNotIn(removed, saved["entries"])
            self.assertEqual(saved["anonymousPages"], {})

            now += 62
            remaining = 2
            self.check()
            self.assertEqual(owner_pages, [1, 2, 2, 1, 2])
            state = self.send("removeOwner", url=owner.lower())
            self.assertEqual([row["repo"] for row in state["catalog"]], ["4FusionLab/ExporTo"])
            saved = json.loads((self.data / "releases.json").read_text())
            self.assertNotIn(owner, saved["discovered"])
            self.assertNotIn(owner, saved["anonymousPages"])

    def test_batch_continues_after_a_failed_download_and_can_retry(self):
        first, second = "4FusionLab/ExporTo", "ACDC4Robot/Fusion360"
        self.start()
        self.send("addOwner", url="ACDC4Robot")
        self.drain()
        self.install(first)
        self.install(second)
        self.github.versions.update({first: "2.0.0", second: "2.0.0"})
        self.check()
        download = f"/{second}/releases/download/v2.0.0/ACDC4Robot-v2.0.0.zip"
        self.github.failures[(download, False)] = URLError("asset connection lost")

        preview = self.send("preview", mode="all")["preview"]
        self.send("applyBatch", token=preview["token"])
        self.drain()
        state = self.send("state")
        self.assertEqual({row["releaseRepo"]: row["version"] for row in state["installed"]},
                         {first: "2.0.0", second: "1.0.0"})
        self.assertEqual([row["status"] for row in state["batchResults"]], ["failed", "completed"])
        self.assertFalse(state["pending"])

        self.github.failures.clear()
        preview = self.send("preview", mode="all")["preview"]
        self.send("applyBatch", token=preview["token"])
        self.drain()
        state = self.send("state")
        self.assertEqual({row["version"] for row in state["installed"]}, {"2.0.0"})
        self.assertEqual(sorted(row["status"] for row in state["batchResults"]), ["completed", "skipped"])

    def test_startup_recovers_profile_files_with_wrong_json_types(self):
        (self.data / "settings.json").write_text("[]")
        (self.data / "releases.json").write_text("null")
        state = self.start()
        self.assertEqual(state["settings"]["owners"], ["4FusionLab"])
        self.assertEqual(state["catalog"], [])
        self.runtime.stop(None)

        (self.data / "settings.json").write_text(json.dumps({
            "owners": None, "organizations": ["ACDC4Robot", 1], "automatic": "false", "interval": "30",
            "targets": [], "installedSources": False,
        }))
        (self.data / "sources.json").write_text('[null, {"repo":"906grown/gridfinity-fusion360"}]')
        (self.data / "releases.json").write_text('{"entries": [], "discovered": {"4FusionLab": [null]}, "checked": []}')
        state = self.start()
        self.assertEqual(state["settings"]["owners"], ["4FusionLab", "ACDC4Robot", "906grown"])
        self.assertTrue(state["settings"]["automatic"])
        self.assertEqual(state["settings"]["interval"], 30)
        self.assertEqual(state["checked"], 0)
        self.assertEqual({item["repo"] for item in self.check()["catalog"]}, set(ADDINS))

    def test_startup_reranks_the_release_cache_and_discards_malformed_assets(self):
        repo = "4FusionLab/ExporTo"
        filename = "ExporTo-windows-x64.zip"
        good = {"name": filename, "browser_download_url": f"https://github.com/{repo}/releases/download/v2.0.0/{filename}",
                "digest": "sha256:" + "a" * 64, "compatible": False}
        assets = [None, 5, [good], {**good, "name": [filename]}, {**good, "browser_download_url": 42},
                  {**good, "browser_download_url": "https://[invalid"}, {**good, "name": "other.zip"}, good]
        (self.data / "releases.json").write_text(json.dumps({"entries": {repo: {
            "tag": "v2.0.0", "assets": assets,
            "history": [None, "bad release", {"tag": "v2.0.0", "assets": assets}, {"tag": "v1.0.0", "assets": False}],
        }}}))
        with patch.object(releases, "PLATFORM", "windows"):
            self.start()
        compatible = {**good, "compatible": True}
        release = self.store.latest[repo]
        self.assertEqual(release["assets"], [compatible])
        self.assertEqual(release["history"], [{"tag": "v2.0.0", "assets": [compatible]}, {"tag": "v1.0.0", "assets": []}])

    def test_startup_falls_back_to_default_profile_when_fusion_profile_lookup_fails(self):
        fallback = self.root / "Профиль Fusion" / "API"
        self.app.executeTextCommand.side_effect = RuntimeError("Profile unavailable")
        with patch.object(self.runtime.inventory, "install_root", return_value=fallback / "AddIns"):
            self.start()
        self.assertIsNone(self.store.profile)
        self.assertEqual(self.store.data, fallback / "CommunityStore")
        self.send("language", language="ru")
        settings = self.store.data / "settings.json"
        self.assertEqual(json.loads(settings.read_text(encoding="utf-8"))["language"], "ru")

    def test_interrupted_update_keeps_previous_package_recoverable(self):
        repo = "4FusionLab/ExporTo"
        for interrupted_after_replace in (False, True):
            with self.subTest(interrupted_after_replace=interrupted_after_replace):
                self.start()
                self.check()
                installed = self.install(repo)["installed"][0]
                target = Path(installed["folder"])
                original_manifest = (target / "ExporTo.manifest").read_bytes()
                original_entry = (target / "ExporTo.py").read_bytes()
                self.github.versions[repo] = "2.0.0"
                self.check()
                preview = self.send("preview", mode="all")["preview"]
                self.send("applyBatch", token=preview["token"])

                rename = Path.rename

                def interrupt_replace(folder, destination):
                    if folder != target and destination == target:
                        if interrupted_after_replace:
                            rename(folder, destination)
                        raise SystemExit("Process interrupted before completing replacement")
                    return rename(folder, destination)

                with patch.object(Path, "rename", interrupt_replace), self.assertRaises(SystemExit):
                    self.drain()
                self.runtime.stop(None)
                backup, = target.parent.glob(".community-store-backups/ExporTo-backup-*")
                self.assertEqual((backup / "ExporTo.manifest").read_bytes(), original_manifest)
                self.assertEqual((backup / "ExporTo.py").read_bytes(), original_entry)
                # A new Fusion process may remember the path, or drop a missing
                # registration. Exercise both paths through the palette.
                if interrupted_after_replace:
                    self.assertEqual(json.loads((target / "ExporTo.manifest").read_bytes())["version"], "2.0.0")
                    target.rename(self.root / "interrupted-ExporTo")
                else:
                    self.assertFalse(target.exists())
                    self.scripts.clear()
                backup.rename(target)
                state = self.start()
                if not state["installed"]:
                    picker = self.ui.createFolderDialog.return_value
                    picker.showDialog.return_value = 1
                    picker.folder = str(target)
                    state = self.send("registerLocal")
                    state = self.send("startup", key=state["installed"][0]["key"], enabled=True)
                restored, = state["installed"]
                self.assertEqual(restored["version"], "1.0.0")
                self.assertEqual(restored["releaseRepo"], repo)
                self.assertTrue(restored["startup"])
                self.assertEqual((target / "ExporTo.py").read_bytes(), original_entry)
                self.runtime.stop(None)
                self.scripts.clear()
                self.timers.clear()
                target.rename(self.root / f"restored-{interrupted_after_replace}")
                self.github.versions[repo] = "1.0.0"

    def test_hot_update_and_rollback_import_helpers_from_the_active_package(self):
        repo = "4FusionLab/ExporTo"
        name = "store_fixture_hot_update_helper"
        self.start()
        self.check()
        installed, = self.install(repo)["installed"]
        target = Path(installed["folder"])
        (target / f"{name}.py").write_text("version = '1.0.0'")
        self.enterContext(patch.object(sys, "path", [str(target), *sys.path]))
        self.addCleanup(sys.modules.pop, name, None)
        self.assertEqual(importlib.import_module(name).version, "1.0.0")

        archive = self.github.archive

        def with_helper(repository, version):
            data = io.BytesIO(archive(repository, version))
            with zipfile.ZipFile(data, "a") as package:
                package.writestr(f"ExporTo/{name}.py", f"version = {version!r}")
            return data.getvalue()

        self.github.archive = with_helper
        self.github.versions[repo] = "2.0.0"
        self.check()
        script = self.scripts.itemByPath(str(target))
        started = []

        def run():
            started.append(importlib.import_module(name).version)
            script.isRunning = True
            return True

        script.run = run
        preview = self.send("preview", mode="all")["preview"]
        self.send("applyBatch", token=preview["token"])
        with patch.object(self.store, "set_preferences", side_effect=OSError("Cannot save settings")):
            self.drain()
        self.assertEqual(started, ["2.0.0", "1.0.0"])
        self.assertEqual(self.store.batch_results[0]["status"], "failed")

    def test_managed_update_preserves_original_when_backup_path_is_redirected(self):
        repo = "4FusionLab/ExporTo"
        self.start()
        self.check()
        installed, = self.install(repo)["installed"]
        target = Path(installed["folder"])
        self.github.versions[repo] = "2.0.0"
        self.check()
        preview = self.send("preview", mode="all")["preview"]
        self.send("applyBatch", token=preview["token"])
        with patch.object(self.runtime.bootstrap, "redirected_directory",
                          side_effect=lambda path: path.name == ".community-store-backups"):
            self.drain()
        restored, = self.send("state")["installed"]
        self.assertEqual(restored["version"], "1.0.0")
        self.assertTrue(restored["running"])
        self.assertTrue(restored["startup"])
        self.assertFalse((target.parent / ".community-store-backups").exists())
        self.assertEqual(self.store.batch_results[0]["status"], "failed")

    def test_update_recovers_when_windows_refuses_to_move_the_original_folder(self):
        repo = "4FusionLab/ExporTo"
        self.start()
        self.check()
        installed = self.install(repo)["installed"][0]
        target = Path(installed["folder"])
        library = target / "dependency.dll"
        library.write_bytes(b"fixture")
        self.github.versions[repo] = "2.0.0"
        self.check()
        preview = self.send("preview", mode="all")["preview"]
        self.send("applyBatch", token=preview["token"])
        rename = Path.rename

        def blocked(folder, destination):
            if folder == target:
                error = PermissionError("DLL is in use")
                error.winerror = 32
                raise error
            return rename(folder, destination)

        with patch.object(Path, "rename", blocked):
            self.drain()
        restored, = self.send("state")["installed"]
        self.assertEqual(restored["version"], "1.0.0")
        self.assertTrue(restored["running"])
        self.assertTrue(restored["startup"])
        self.assertEqual(library.read_bytes(), b"fixture")
        self.assertFalse(list(target.parent.glob(".community-store-backups/ExporTo-backup-*")))
        # Retrying after the library is released replaces the same registration.
        preview = self.send("preview", mode="all")["preview"]
        self.send("applyBatch", token=preview["token"])
        self.drain()
        self.assertEqual(self.send("state")["installed"][0]["version"], "2.0.0")

    def test_windows_rollback_preserves_backup_when_replacement_folder_is_locked(self):
        repo = "4FusionLab/ExporTo"
        self.start()
        self.check()
        installed, = self.install(repo)["installed"]
        target = Path(installed["folder"])
        previous = (target / "ExporTo.py").read_bytes()
        self.github.versions[repo] = "2.0.0"
        self.check()
        preview = self.send("preview", mode="all")["preview"]
        self.send("applyBatch", token=preview["token"])
        rename = Path.rename

        def blocked(folder, destination):
            if folder == target and destination.name == "failed-package":
                error = PermissionError("DLL is in use")
                error.winerror = 32
                raise error
            return rename(folder, destination)

        with (patch.object(Path, "rename", blocked),
              patch.object(self.store, "set_preferences", side_effect=OSError("Cannot save settings"))):
            self.drain()
        backup, = target.parent.glob(".community-store-backups/ExporTo-backup-*")
        self.assertEqual((backup / "ExporTo.py").read_bytes(), previous)
        self.assertTrue(self.scripts.itemByPath(str(target)).isVisible)
        self.assertFalse(self.scripts.itemByPath(str(target)).isRunning)
        self.assertFalse(self.scripts.itemByPath(str(target)).isRunOnStartup)
        result, = self.store.batch_results
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["message"].key, "runtime.rollback_failed")
        self.assertEqual(result["message"].values["backup"], backup)

    def test_windows_managed_update_keeps_registration_after_case_only_rename(self):
        if sys.platform != "win32":
            if not (self.root / "PROFILE").is_dir():
                self.skipTest("Requires a case-insensitive filesystem")
            self.enterContext(patch.object(os.path, "normcase", side_effect=str.lower))
        repo = "4FusionLab/ExporTo"
        self.start()
        self.check()
        installed, = self.install(repo)["installed"]
        original = self.scripts.itemByPath(installed["folder"])
        self.github.versions[repo] = "2.0.0"
        self.github.package_names[repo] = "exporto"
        self.check()
        preview = self.send("preview", mode="all")["preview"]
        self.send("applyBatch", token=preview["token"])
        self.drain()
        updated, = self.send("state")["installed"]
        self.assertEqual(updated["version"], "2.0.0")
        self.assertEqual(updated["folder"], installed["folder"])
        self.assertEqual(list(self.scripts), [original])
        self.assertTrue(original.isRunning)
        self.assertTrue(original.isRunOnStartup)
        self.assertTrue(updated["writable"])
