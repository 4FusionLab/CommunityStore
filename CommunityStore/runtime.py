"""Fusion palette and main-thread adapter for the Community Store."""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import uuid4

import adsk.core

from . import bootstrap, credentials, diagnostics, github_auth, inventory, media, profiles, releases, reporting, self_update, trusted_stores, versions
from .config import COMMAND_ID, DISPLAY_NAME, GITHUB_OAUTH_CLIENT_ID
from .core import github_owner, manifest_id
from .localization import LANGUAGES, SYSTEM, Message, catalog, error_message, palette_catalog, selected_language, translate

CODE_ROOT = Path(__file__).resolve().parent
PALETTE_ID = "4FusionLab.CommunityStore.Palette"
PALETTE_WIDTH, PALETTE_HEIGHT = 1000, 780
PALETTE_MIN_WIDTH, PALETTE_MIN_HEIGHT = 300, 360
CHECK_INTERVALS = profiles.CHECK_INTERVALS
PALETTE_ACTIONS = frozenset((
    "state", "openUrl", "report", "language", "githubSignIn", "githubCopyCode", "githubOpenDevice",
    "githubCancel", "githubSignOut", "githubProfile",
    "check", "native", "preferences", "dismissGuide", "resetGuide", "addOwner", "removeOwner",
    "release", "openFolder", "selfUpdate", "history", "target", "registerLocal", "preview",
    "exportProfile", "importProfile", "applyProfile",
    "applyBatch", "startup", "remove", "openRemovalArchive", "reload", "enable", "disable", "install", "exportLogs",
))
_controller = None


class Receive(adsk.core.HTMLEventHandler):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller

    def notify(self, args):
        if not args.action.startswith("communityStore:"):
            return
        if args.action == "communityStore:media":
            try:
                self.controller.load_media(json.loads(args.data or "{}"))
            except (TypeError, ValueError):
                pass
            args.returnData = "OK"
            return
        action = args.action.removeprefix("communityStore:")
        label = action if action in PALETTE_ACTIONS else "unknown"
        if action != "state":
            diagnostics.event("palette.action", action=label)
        try:
            self.controller.dispatch(action, json.loads(args.data or "{}"))
        except Exception as error:
            self.controller.notice = error_message(error)
            diagnostics.failure("palette.action_failed", error, action=label)
        try:
            state = self.controller.snapshot()
            if action in ("language", "applyProfile"):
                self.controller.update_command_title(state)
            args.returnData = json.dumps(state, ensure_ascii=False)
        except Exception as error:
            diagnostics.failure("palette.response_failed", error, action=label)
            raise


class Open(adsk.core.CommandCreatedEventHandler):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller

    def notify(self, args):
        try:
            self.controller.show()
        except Exception as error:
            diagnostics.failure("palette.open_failed", error)
            raise


class WorkCompleted(adsk.core.CustomEventHandler):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller

    def notify(self, args):
        try:
            self.controller.process_results()
        except Exception as error:
            diagnostics.failure("worker.results_failed", error)
            raise


class Store:
    catalog_scan = None
    anonymous_scan = None

    def __init__(self, root, version, update_notice, update_disabled):
        self.root = root
        self.addin_root = root / "AddIns"
        self.version = version
        self.update_notice = update_notice
        self.update_disabled = update_disabled
        self.update_release = None
        self.update_downloading = False
        self.update_ready = False
        if not self.update_disabled:
            try:
                state = bootstrap.read_state(bootstrap.state_dir(root))
                pending = self_update.pending_notice(state)
                if pending:
                    self.update_ready = True
                    self.update_notice = pending
            except (OSError, ValueError):
                pass  # The loader already reports an unreadable state.
        self.app = adsk.core.Application.get()
        diagnostics.event("store.starting", store=diagnostics.version(version),
                          fusion=diagnostics.version(self.app.version), os=sys.platform,
                          update_disabled=diagnostics.message_key(update_disabled),
                          update_notice=diagnostics.message_key(self.update_notice))
        self.ui = self.app.userInterface
        try:
            profile = Path((self.app.executeTextCommand("Paths.UserDirectory") or "").strip())
        except (OSError, RuntimeError) as error:
            diagnostics.failure("store.profile_lookup_failed", error)
            profile = None
        self.profile = profile if profile is not None and profile.is_absolute() else None
        self.data = (self.profile or inventory.install_root().parent) / "CommunityStore"
        store_cache = self.read_data(trusted_stores.FILENAME, {})
        self.cached_trusted_owners = None
        try:
            if store_cache:
                self.cached_trusted_owners = trusted_stores.parse(store_cache)
        except ValueError as error:
            diagnostics.failure("catalog.trusted_sources_cache_invalid", error)
        self.trusted_owners = (trusted_stores.FALLBACK_OWNERS if self.cached_trusted_owners is None
                               else self.cached_trusted_owners)
        self.trusted_sources_pending = False
        self.trusted_sources_retry = 0
        previous_settings = self.read_data("settings.json", {})
        subscriptions = previous_settings.get("ownerSubscriptions")
        if not isinstance(subscriptions, list):
            configured_owners = previous_settings.get("owners")
            if not isinstance(configured_owners, list):
                old_sources = self.read_data("sources.json", [])
                organizations = previous_settings.pop("organizations", [])
                configured_owners = list(organizations) if isinstance(organizations, list) else []
                configured_owners.extend(source["repo"].split("/")[0] for source in old_sources
                                         if isinstance(source, dict) and isinstance(source.get("repo"), str))
            fallback = {owner.casefold() for owner in trusted_stores.FALLBACK_OWNERS}
            subscriptions = [owner for owner in trusted_stores.merge_owners(configured_owners)
                             if owner.casefold() not in fallback]
        subscriptions = trusted_stores.merge_owners(subscriptions)
        self.preferences = {"automatic": True, "interval": 30, "language": SYSTEM,
                            **previous_settings, "ownerSubscriptions": subscriptions,
                            "owners": trusted_stores.merge_owners(self.trusted_owners, subscriptions)}
        if self.preferences["language"] not in (SYSTEM, *LANGUAGES):
            self.preferences["language"] = SYSTEM
        if not isinstance(self.preferences["automatic"], bool):
            self.preferences["automatic"] = True
        if type(self.preferences["interval"]) is not int or self.preferences["interval"] not in CHECK_INTERVALS:
            self.preferences["interval"] = 30
        for key in ("targets", "installedSources"):
            if key in self.preferences:
                records = self.preferences[key]
                self.preferences[key] = (
                    {name: value for name, value in records.items() if isinstance(value, dict)}
                    if isinstance(records, dict) else {}
                )
        self.discovery_errors = {}
        cache = self.read_data("releases.json", {})
        entries = cache.get("entries")
        self.latest = ({repo: releases.refresh_cached_release(entry, repo) for repo, entry in entries.items() if isinstance(entry, dict)}
                       if isinstance(entries, dict) else {})
        for release in self.latest.values():
            error = release.get("error")
            if isinstance(error, str) and re.match(r"HTTP Error (403|429): .*rate limit", error, re.I):
                release.pop("error")
        releases.restore_rate_limit(cache.get("retryAt"))
        discovered = cache.get("discovered")
        self.discovered = {}
        if isinstance(discovered, dict):
            for owner, sources in discovered.items():
                if isinstance(sources, list):
                    self.discovered[owner] = list(self.cached_sources(sources).values())
        scan = cache.get("anonymousScan")
        if (isinstance(scan, list) and len(scan) == 2 and
                all(isinstance(group, list) and all(isinstance(item, str) for item in group) for group in scan)):
            pages = {}
            saved_pages = cache.get("anonymousPages")
            if isinstance(saved_pages, dict):
                for owner, checkpoint in saved_pages.items():
                    if (isinstance(checkpoint, list) and len(checkpoint) == 2
                            and type(checkpoint[0]) is int and checkpoint[0] > 1
                            and isinstance(checkpoint[1], list)):
                        pages[owner] = (checkpoint[0], self.cached_sources(checkpoint[1]))
            self.anonymous_scan = self.scan_for_owners(
                (set(scan[0]).intersection(self.discovered), set(scan[1]).intersection(self.latest), pages),
                self.preferences["owners"],
            )
        checked = cache.get("checked")
        self.last_check = checked if type(checked) is int else 0
        self.check_again = False
        self.history_requested = set()
        self.github_client_id = os.environ.get("COMMUNITY_STORE_GITHUB_CLIENT_ID", GITHUB_OAUTH_CLIENT_ID).strip()
        self.github_session = None
        self.github_attempt = None
        self.github_code = ""
        self.github_error = ""
        self.github_copy_feedback = ""
        self.restore_github_session()
        self.catalog_scan = None if self.github_session else self.anonymous_scan
        self.notice = ""
        self.checking = False
        self.pending = None
        self.profile_import = None
        self.profile_preview = None
        self.batch = None
        self.batch_total = 0
        self.preview = None
        self.batch_results = []
        self.self_progress = None
        self.operations = {}
        self.self_reload_pending = False
        self.rows = {}
        self.scripts_by_key = {}
        self.palette = None
        self.palette_needs_placement = False
        self.receiver = None
        self.command = None
        self.control = None
        self.closed = threading.Event()
        self.schedule_changed = threading.Event()
        self.results = queue.SimpleQueue()
        self.artwork = media.ArtworkCache(self.data / "artwork")
        self.artwork_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="CommunityStoreArtwork")
        self.artwork_requests = set()
        self.event_id = COMMAND_ID + "." + uuid4().hex
        self.event = None
        self.refresh_inventory()

    def read_data(self, name, default):
        try:
            data = json.loads((self.data / name).read_text(encoding="utf-8"))
            if not isinstance(data, type(default)):
                raise ValueError("Unexpected store data type")
            return data
        except FileNotFoundError:
            return default
        except (OSError, ValueError) as error:
            diagnostics.failure("store.data_read_failed", error, file=name)
            return default

    def write_data(self, name, data):
        self.data.mkdir(parents=True, exist_ok=True)
        bootstrap.write_json(self.data / name, data, indent=2)

    @staticmethod
    def cached_sources(sources):
        return {source["repo"].casefold(): source for source in sources if isinstance(source, dict)
                and isinstance(source.get("repo"), str) and isinstance(source.get("name"), str)}

    def scan_for_owners(self, scan, owners):
        if scan is None:
            return None
        done_owners, done_repos, pages = scan
        active_repos = {source["repo"] for source in self.sources(owners=owners)}
        return (done_owners.intersection(owners), done_repos.intersection(active_repos),
                {owner: checkpoint for owner, checkpoint in pages.items() if owner in owners})

    def save_release_cache(self):
        retry = releases.retry_at()
        scan = self.anonymous_scan
        self.write_data("releases.json", {
            "entries": self.latest, "discovered": self.discovered, "checked": self.last_check,
            "retryAt": retry if retry > time.time() else 0,
            "anonymousScan": [sorted(scan[0]), sorted(scan[1])] if scan else None,
            "anonymousPages": {owner: [page, list(sources.values())]
                               for owner, (page, sources) in scan[2].items()} if scan else {},
        })

    def sources(self, discovered=None, owners=None):
        discovered = self.discovered if discovered is None else discovered
        owners = self.preferences["owners"] if owners is None else owners
        indexed = {source["repo"].casefold(): source for owner in owners for source in discovered.get(owner, [])}
        bindings = self.preferences.get("installedSources", {})
        for key, row in getattr(self, "rows", {}).items():
            saved = bindings.get(key, {})
            repo = self.repository_for(row)
            if repo and saved.get("profileInstalled") and saved.get("id") == row["id"]:
                indexed.setdefault(repo.casefold(), {"repo": repo, "name": repo.split("/")[1]})
        return list(indexed.values())

    def queue_check(self):
        if self.checking:
            self.check_again = True
        else:
            self.check()

    def start(self):
        self.event = self.app.registerCustomEvent(self.event_id)
        self.completion = WorkCompleted(self)
        self.event.add(self.completion)
        self.command = self.ui.commandDefinitions.addButtonDefinition(
            COMMAND_ID, DISPLAY_NAME, catalog(self.language)["runtime.github_addins"],
            str(CODE_ROOT / "toolbar-open-catalog"),
        )
        self.open_handler = Open(self)
        self.command.commandCreated.add(self.open_handler)
        panel = self.ui.allToolbarPanels.itemById("SolidScriptsAddinsPanel")
        if panel:
            self.control = panel.controls.addCommand(self.command)
            self.control.isPromoted = True
        self.update_command_title(self.snapshot())
        self.load_github_profile()
        self.refresh_trusted_sources()
        threading.Thread(target=self.schedule_checks, daemon=True).start()

    def schedule_checks(self):
        while not self.closed.is_set():
            preferences = self.preferences
            if preferences["automatic"]:
                self.complete("requestCheck", None)
            timeout = preferences["interval"] * 60 if preferences["automatic"] else None
            self.schedule_changed.wait(timeout)
            self.schedule_changed.clear()

    def complete(self, kind, payload):
        if not self.closed.is_set():
            self.results.put((kind, payload))
            self.app.fireCustomEvent(self.event_id)

    def show(self):
        self.refresh_inventory()
        self.refresh_trusted_sources()
        self.prefetch_installed_histories()
        created = not self.palette or not self.palette.isValid
        if created:
            self.palette = self.ui.palettes.add(
                PALETTE_ID, DISPLAY_NAME, (CODE_ROOT / "ui/index.html").as_uri(), False, True, True,
                PALETTE_WIDTH - 1, PALETTE_HEIGHT - 1, True,
            )
            if not self.palette:
                raise RuntimeError(Message("runtime.palette_failed"))
            self.palette.setMinimumSize(PALETTE_MIN_WIDTH, PALETTE_MIN_HEIGHT)
            self.palette_needs_placement = True
            self.receiver = Receive(self)
            self.palette.incomingFromHTML.add(self.receiver)
        self.palette.isVisible = True
        diagnostics.event("palette.opened", created=created)
        if created:
            # A newly shown Fusion WebView can keep its hidden size until a resize.
            resize = threading.Timer(0.2, self.complete, args=("layout", None))
            resize.daemon = True
            resize.start()
        else:
            self.publish_state()

    def place_palette(self, display):
        if not self.palette_needs_placement or not self.palette.isVisible or not isinstance(display, dict):
            return
        left, top, width, height = (display.get(key) for key in ("left", "top", "width", "height"))
        if not all(type(value) is int for value in (left, top, width, height)) or width <= 0 or height <= 0:
            return
        self.palette_needs_placement = False
        # Leave room for the native title bar and display edges on smaller screens.
        palette_width = min(PALETTE_WIDTH, max(PALETTE_MIN_WIDTH, width - 48))
        palette_height = min(PALETTE_HEIGHT, max(PALETTE_MIN_HEIGHT, height - 48))
        self.palette.setPosition(left + max(0, (width - palette_width) // 2),
                                 top + max(0, (height - palette_height) // 2))
        self.palette.setSize(palette_width, palette_height)

    def update_command_title(self, state):
        if not self.command or not self.command.isValid:
            return
        count = state["updateCount"]
        title = f"{DISPLAY_NAME} ({count})" if count else DISPLAY_NAME
        if self.command.name != title:
            self.command.name = title
        key = "runtime.updates_available" if count else "runtime.github_addins"
        tooltip = catalog(self.language)[key].format(count=count)
        if self.command.tooltip != tooltip:
            self.command.tooltip = tooltip

    def publish_state(self):
        state = self.snapshot()
        self.update_command_title(state)
        if self.palette and self.palette.isValid and self.palette.isVisible:
            self.palette.sendInfoToHTML("state", json.dumps(state, ensure_ascii=False))

    def dispose(self):
        diagnostics.event("store.stopping")
        self.closed.set()
        if self.github_attempt:
            self.github_attempt.set()
        if self.github_session:
            self.github_session.ended.set()
        self.artwork_pool.shutdown(wait=False, cancel_futures=True)
        self.schedule_changed.set()
        for item in (self.palette, self.control, self.command):
            try:
                if item and item.isValid:
                    item.deleteMe()
            except Exception as error:
                diagnostics.failure("store.control_cleanup_failed", error)
        if self.event:
            try:
                self.event.remove(self.completion)
            finally:
                self.app.unregisterCustomEvent(self.event_id)
        if self.pending and self.pending.get("work") and not self.pending.get("applying"):
            shutil.rmtree(self.pending["work"], ignore_errors=True)
        while not self.results.empty():
            kind, payload = self.results.get_nowait()
            if kind == "staged":
                shutil.rmtree(payload[0], ignore_errors=True)

    def refresh_inventory(self):
        rows, self.scripts_by_key = inventory.from_fusion(self.app.scripts, self.addin_root)
        self.rows = {row["key"]: row for row in rows}
        for row in rows:
            if Path(row["folder"]).resolve() == self.root:
                row["self"] = True
                row["running"] = True
                row["version"] = self.version

    def lifecycle(self, row, enable):
        script = self.scripts_by_key.get(row["key"])
        if not script or not script.isValid:
            script = self.app.scripts.itemByPath(row["folder"])
        if not script:
            raise ValueError(Message("runtime.not_registered"))
        if bool(script.isRunning) == enable:
            return
        diagnostics.event("addin.lifecycle_requested", enabled=enable)
        success = script.run() if enable else script.stop()
        if not success:
            raise RuntimeError(Message("runtime.lifecycle_failed_start" if enable else "runtime.lifecycle_failed_stop"))

    def observe(self, row, enable, callback=None):
        # Loading can complete after the text command returns. Check on Fusion's main thread.
        self.operations[row["key"]] = (row, enable, time.monotonic() + 12, callback)
        if len(self.operations) == 1:
            self.retry_observation()

    def retry_observation(self):
        retry = threading.Timer(0.35, self.complete, args=("observe", None))
        retry.daemon = True
        retry.start()

    def observe_operations(self):
        self.refresh_inventory()
        for key, (row, enable, deadline, callback) in tuple(self.operations.items()):
            running = self.rows.get(key, {}).get("running")
            succeeded = running is enable
            if succeeded or time.monotonic() >= deadline:
                del self.operations[key]
                diagnostics.event("addin.lifecycle_finished", enabled=enable, confirmed=succeeded)
                if callback:
                    callback(succeeded)
                elif not succeeded:
                    self.notice = Message("runtime.state_unconfirmed", name=row["name"])
        if self.operations:
            self.retry_observation()

    def reload_addin(self, row):
        key = row["key"]
        if key in self.operations or self.self_reload_pending:
            return
        if not self.is_idle():
            raise RuntimeError(Message("runtime.close_command"))
        name = row["name"]
        if row.get("self"):
            if self.operations or self.update_downloading:
                raise RuntimeError(Message("runtime.wait_install"))
            self.self_reload_pending = True
            timer = threading.Timer(0.15, self.complete, args=("reloadSelf", key))
            timer.daemon = True
            timer.start()
            return

        def stopped(confirmed):
            if not confirmed:
                self.notice = Message("runtime.state_unconfirmed", name=name)
                return
            self.lifecycle(row, True)
            self.observe(row, True)

        running = row["running"]
        self.lifecycle(row, not running)
        self.observe(row, not running, stopped if running else None)

    def restart_store(self, key):
        self.self_reload_pending = False
        if not self.is_idle() or self.pending or self.batch or self.operations or self.update_downloading:
            self.notice = Message("runtime.state_unconfirmed", name=DISPLAY_NAME)
            return
        script = self.scripts_by_key.get(key)
        if not script or not script.isValid or not script.stop():
            self.notice = Message("runtime.lifecycle_failed_stop")
            return
        if not script.run(False):
            self.ui.messageBox(str(Message("runtime.lifecycle_failed_start")))

    def restore_github_session(self):
        if not self.github_client_id:
            return
        if not github_auth.CLIENT_ID.fullmatch(self.github_client_id):
            self.github_error = Message("github.app_invalid")
            return
        try:
            token = credentials.open_store(self.github_client_id).load()
            if token:
                if not github_auth.TOKEN.fullmatch(token):
                    raise credentials.CredentialError
                self.github_session = releases.GitHubSession(token)
        except credentials.CredentialError as error:
            diagnostics.failure("github.vault_read_failed", error)
            self.github_error = Message("github.vault_read_failed")

    def sign_in_github(self):
        client_id = self.github_client_id
        if self.github_attempt or self.github_session or not github_auth.CLIENT_ID.fullmatch(client_id):
            return
        self.github_error = ""
        attempt = self.github_attempt = threading.Event()

        def worker():
            token, error = None, ""
            try:
                token = github_auth.authorize(client_id, attempt,
                                              lambda code: self.complete("githubCode", (attempt, code)))
            except github_auth.AuthorizationError as failure:
                diagnostics.failure("github.authorization_failed", failure)
                error = error_message(failure)
            self.complete("githubAuthorized", (attempt, token, error))

        threading.Thread(target=worker, daemon=True).start()

    def finish_github_sign_in(self, attempt, token, error):
        if self.github_attempt is not attempt:
            return
        self.github_attempt = None
        self.github_code = ""
        self.github_error = error
        self.github_copy_feedback = ""
        if token:
            try:
                credentials.open_store(self.github_client_id).save(token)
            except credentials.CredentialError as failure:
                diagnostics.failure("github.vault_write_failed", failure)
                self.github_error = Message("github.vault_write_failed")
                return
            self.github_session = releases.GitHubSession(token)
            self.catalog_scan = None
            diagnostics.event("github.authorized")
            self.load_github_profile()
            self.queue_check()

    def load_github_profile(self):
        session = self.github_session
        if not session or session.profile or session.profile_loading:
            return
        session.profile_loading = True

        def worker():
            profile = None
            try:
                profile = releases.authenticated_profile(session)
            except (OSError, ValueError, releases.RateLimited) as error:
                diagnostics.failure("github.profile_lookup_failed", error)
            self.complete("githubProfile", (session, profile))

        threading.Thread(target=worker, daemon=True).start()

    def sign_out_github(self, *, expired=False):
        if not self.github_session:
            return
        try:
            credentials.open_store(self.github_client_id).delete()
        except credentials.CredentialError as error:
            diagnostics.failure("github.vault_delete_failed", error)
            self.github_error = Message("github.vault_delete_failed")
            if not expired:
                return
        else:
            self.github_error = Message("github.session_expired") if expired else ""
        self.github_session.ended.set()
        self.github_session = None
        self.catalog_scan = self.anonymous_scan
        diagnostics.event("github.signed_out", expired=expired)
        self.queue_check()

    def copy_github_code(self):
        if not self.github_attempt or not self.github_code:
            return
        options = {}
        if sys.platform == "darwin":
            command = "/usr/bin/pbcopy"
        elif sys.platform == "win32":
            command = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "clip.exe")
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            self.github_copy_feedback = Message("github.copy_failed")
            return
        try:
            subprocess.run([command], input=self.github_code.encode("ascii"), check=True, timeout=2,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
        except (OSError, subprocess.SubprocessError):
            self.github_copy_feedback = Message("github.copy_failed")
        else:
            self.github_copy_feedback = Message("github.code_copied")

    def check(self):
        self.refresh_trusted_sources()
        session = self.github_session
        if self.checking or releases.retry_at(session) > time.time():
            return
        self.checking = True
        owners = tuple(self.preferences["owners"])
        cached = dict(self.discovered)
        previous = dict(self.latest)
        owner_progress, repo_progress, page_progress = self.catalog_scan or (set(), set(), {})
        done_owners, done_repos = owner_progress.intersection(owners), repo_progress.copy()
        pages = {owner: page_progress[owner] for owner in owners if owner in page_progress}
        errors = {owner: self.discovery_errors[owner] for owner in done_owners if owner in self.discovery_errors}
        installed = [(row, self.repository_for(row)) for row in self.rows.values() if not row.get("self")]
        requested = self.history_requested.copy()
        check_store = not (self.catalog_scan or self.update_disabled or self.update_ready or self.update_downloading)
        diagnostics.event("catalog.check_started", connected=bool(session), owners=len(owners),
                          histories=len(requested), check_store=check_store)

        def installed_source(source, entry):
            repo = source["repo"].casefold()
            candidate = {**source, **entry.get("identity", {})}
            return any(repo == bound.casefold() if bound else inventory.matches(candidate, row)
                       for row, bound in installed)

        def load_history(repo, entry):
            try:
                return {**entry, **releases.release_history(repo, entry["tag"])}
            except releases.RateLimited:
                raise
            except Exception as error:
                diagnostics.failure("catalog.release_history_failed", error)
                return entry

        @releases.use_session(session)
        def lookup(source):
            repo = source["repo"]
            if self.closed.is_set():
                return repo, None, False
            cached_entry = previous.get(repo, {})
            try:
                entry = releases.latest(source, cached_entry, with_history=repo in requested,
                                        refresh_history=installed_source(source, cached_entry))
            except releases.RateLimited:
                return repo, None, False
            except Exception as error:
                diagnostics.failure("catalog.release_lookup_failed", error)
                return repo, {**previous.get(repo, {}), "error": str(error)}, False
            if entry.get("isAddin") and "history" not in entry and installed_source(source, entry):
                try:
                    entry = load_history(repo, entry)
                except releases.RateLimited:
                    return repo, entry, False
            return repo, entry, True

        @releases.use_session(session)
        def worker():
            # Populate legacy caches before unrelated discovery or self-update requests.
            for source in self.sources(cached, owners):
                repo = source["repo"]
                entry = previous.get(repo, {})
                if (self.closed.is_set() or not entry.get("isAddin") or not entry.get("tag")
                        or "history" in entry or repo in requested or not installed_source(source, entry)):
                    continue
                try:
                    warmed = load_history(repo, entry)
                except releases.RateLimited:
                    break
                if warmed is not entry:
                    previous[repo] = warmed
                    self.complete("installedReleases", (cached, {repo: warmed}))
            if check_store:
                try:
                    release = self_update.discover(self.root, self.version, self.closed)
                    self.complete("selfRelease", (release, ""))
                except releases.RateLimited:
                    pass
                except Exception as error:
                    diagnostics.failure("store.update_lookup_failed", error)
                    self.complete("selfRelease", (None, error_message(error)))
            discovered = {owner: cached.get(owner, []) for owner in owners}
            for owner in owners:
                if owner in done_owners:
                    continue
                page, saved = pages.get(owner, (1, {}))
                found = saved.copy()

                def advance(next_page):
                    pages[owner] = (next_page, found)

                try:
                    for source in releases.public_repositories(owner, page=page, advance=advance):
                        found[source["repo"].casefold()] = source
                except releases.RateLimited:
                    break
                except Exception as error:
                    diagnostics.failure("catalog.discovery_failed", error)
                    errors[owner] = error_message(error)
                    if owner in pages:
                        continue
                else:
                    discovered[owner] = list(found.values())
                    pages.pop(owner, None)
                    errors.pop(owner, None)
                done_owners.add(owner)
            completed = set()
            sources = {source["repo"]: source for source in self.sources(discovered, owners)}
            result = {repo: previous[repo] for repo in sources if repo in previous}
            priority, pending = [], []
            for repo, source in sources.items():
                entry = previous.get(repo, {})
                local = installed_source(source, entry)
                if repo not in done_repos or repo in requested or (local and "history" not in entry):
                    (priority if local else pending).append(source)
            if releases.retry_at(session) <= time.time() and not (session and session.ended.is_set()):
                def collect(outcomes, *, publish_early=False):
                    for repo, entry, checked in outcomes:
                        if entry is None:
                            continue
                        result[repo] = entry
                        if checked or releases.retry_at(session) <= time.time():
                            done_repos.add(repo)
                        if checked and repo in requested:
                            completed.add(repo)
                        if publish_early and entry != previous.get(repo):
                            self.complete("installedReleases", (discovered, {repo: entry}))

                # Finish each anonymous repository's multi-request lookup before
                # spending the remaining quota on the next repository.
                with ThreadPoolExecutor(max_workers=4 if session else 1) as pool:
                    early = [pool.submit(lookup, source) for source in priority]
                    collect((future.result() for future in as_completed(early)), publish_early=True)
                    collect(pool.map(lookup, pending))
            scanning = done_owners != set(owners) or not done_repos.issuperset(sources)
            diagnostics.event("catalog.check_finished", repositories=len(result),
                              discovery_errors=len(errors), rate_limited=releases.retry_at(session) > time.time(),
                              session_ended=bool(session and session.ended.is_set()))
            self.complete("checked", (discovered, errors, result, completed, session,
                                      (done_owners, done_repos, pages) if scanning else None))

        threading.Thread(target=worker, daemon=True).start()

    def refresh_trusted_sources(self):
        if self.closed.is_set() or self.trusted_sources_pending or time.monotonic() < self.trusted_sources_retry:
            return
        self.trusted_sources_pending = True

        def worker():
            owners = None
            try:
                owners = trusted_stores.fetch()
            except Exception as error:
                diagnostics.failure("catalog.trusted_sources_lookup_failed", error)
            self.complete("trustedSources", owners)

        threading.Thread(target=worker, daemon=True).start()

    def apply_trusted_sources(self, owners):
        self.trusted_sources_pending = False
        self.trusted_sources_retry = time.monotonic() + 60
        if owners is None:
            return False
        if owners != self.cached_trusted_owners:
            try:
                self.write_data(trusted_stores.FILENAME, {"schemaVersion": 1, "owners": list(owners)})
            except OSError as error:
                diagnostics.failure("catalog.trusted_sources_cache_failed", error)
            else:
                self.cached_trusted_owners = owners
        if owners == self.trusted_owners:
            return False
        self.trusted_owners = owners
        active = trusted_stores.merge_owners(owners, self.preferences["ownerSubscriptions"])
        if active != self.preferences["owners"]:
            self.preferences = {**self.preferences, "owners": active}
            self.prune_sources()
            self.queue_check()
        return True

    def prune_sources(self):
        owners = self.preferences["owners"]
        self.catalog_scan = self.scan_for_owners(self.catalog_scan, owners)
        self.anonymous_scan = self.scan_for_owners(self.anonymous_scan, owners)
        self.discovered = {owner: sources for owner, sources in self.discovered.items() if owner in owners}
        self.discovery_errors = {owner: error for owner, error in self.discovery_errors.items() if owner in owners}
        active_repos = {source["repo"] for source in self.sources()}
        self.latest = {repo: entry for repo, entry in self.latest.items() if repo in active_repos}

    def prefetch_installed_histories(self, *, inventory_changed=False):
        if self.checking and not inventory_changed:
            return
        if any(row["repo"] and not row["historyLoaded"] for row in self.installed_rows(self.marketplace())):
            self.queue_check()

    def load_media(self, data):
        if not isinstance(data, dict):
            return
        ticket = data.get("ticket")
        key = data.get("key")
        kind = data.get("kind")
        if not isinstance(ticket, str) or not ticket.isdecimal() or len(ticket) > 12 or not isinstance(key, str):
            return
        if ticket in self.artwork_requests:
            return
        if len(self.artwork_requests) >= 20:
            self.complete("artworkRejected", ticket)
            return
        if kind == "githubAvatar":
            profile = self.github_session.profile if self.github_session else None
            if not profile or profile["login"] != key or not profile["avatar"]:
                self.complete("artworkRejected", ticket)
                return
            avatar = profile["avatar"]
            work = lambda: {"avatar": self.artwork.avatar(avatar)}
        elif kind == "avatar":
            avatar = next((item["ownerAvatar"] for item in self.discovered.get(key, []) if item.get("ownerAvatar")), "")
            work = lambda: {"avatar": self.artwork.avatar(avatar)} if avatar else {}
        elif kind in ("card", "detail"):
            source = next((item for item in self.sources() if item["repo"] == key), None)
            release = self.latest.get(key, {})
            if not source or not release.get("tag") or not release.get("isAddin"):
                self.complete("artworkRejected", ticket)
                return
            entry = {**source, **release.get("identity", {}), "tag": release["tag"],
                     "imageAssets": release.get("imageAssets", [])}
            language = self.language
            def work():
                publish_readme = (lambda fields: self.complete("artworkFields", (ticket, fields))) if kind == "detail" else None
                return self.artwork.artwork(entry, language, kind == "detail", publish_readme=publish_readme)
        else:
            return

        self.artwork_requests.add(ticket)

        def worker():
            result = {}
            try:
                result = work()
            except Exception as error:
                # Artwork failures must leave the catalog and operations usable.
                diagnostics.failure("media.load_failed", error, kind=kind)
            self.complete("artwork", (ticket, result))

        self.artwork_pool.submit(worker)

    def publish_artwork(self, ticket, result, *, done=True):
        if done:
            self.artwork_requests.discard(ticket)
        if not (self.palette and self.palette.isValid):
            return
        for field, value in result.items():
            if value:
                self.palette.sendInfoToHTML("artwork", json.dumps({"ticket": ticket, "field": field, "value": value}))
        if done:
            self.palette.sendInfoToHTML("artwork", json.dumps({"ticket": ticket, "done": True}))

    def process_results(self):
        state_changed = False
        while not self.results.empty():
            kind, payload = self.results.get_nowait()
            state_changed |= kind not in ("artwork", "artworkFields", "artworkRejected", "trustedSources")
            try:
                if kind == "artwork":
                    self.publish_artwork(*payload)
                elif kind == "artworkFields":
                    self.publish_artwork(*payload, done=False)
                elif kind == "artworkRejected":
                    self.publish_artwork(payload, {})
                elif kind == "requestCheck":
                    if self.preferences["automatic"]:
                        self.check()
                elif kind == "profileMetadata":
                    token, metadata = payload
                    if self.profile_import and self.profile_import["token"] == token:
                        self.profile_import["metadata"] = metadata
                        self.profile_preview = self.profile_plan()
                        self.notice = ""
                elif kind == "trustedSources":
                    state_changed |= self.apply_trusted_sources(payload)
                elif kind == "layout":
                    if self.palette_needs_placement and self.palette and self.palette.isValid:
                        self.palette.setSize(PALETTE_WIDTH, PALETTE_HEIGHT)
                elif kind == "githubCode":
                    attempt, code = payload
                    if self.github_attempt is attempt:
                        self.github_code = code
                elif kind == "githubAuthorized":
                    self.finish_github_sign_in(*payload)
                elif kind == "githubProfile":
                    session, profile = payload
                    if session is self.github_session:
                        session.profile_loading = False
                        session.profile = profile
                        if session.ended.is_set():
                            self.sign_out_github(expired=True)
                elif kind == "selfRelease":
                    if not (self.update_ready or self.update_downloading):
                        release, error = payload
                        if error:
                            self.update_notice = Message("runtime.self_update_failed", error=error)
                        else:
                            self.update_release = release
                            if getattr(self.update_notice, "key", None) == "runtime.self_update_failed":
                                self.update_notice = ""
                elif kind == "selfStaged":
                    self.update_downloading = False
                    self.self_progress = None
                    notice, error = payload
                    if error:
                        self.update_notice = Message("runtime.self_update_failed", error=error)
                    else:
                        self.update_ready = bool(notice)
                        self.update_release = None
                        self.update_notice = notice
                elif kind == "installedReleases":
                    discovered, entries = payload
                    self.discovered.update({owner: discovered[owner] for owner in self.preferences["owners"]
                                            if owner in discovered})
                    self.latest.update({source["repo"]: entries[source["repo"]] for source in self.sources()
                                        if source["repo"] in entries})
                elif kind == "checked":
                    self.checking = False
                    self.refresh_inventory()
                    discovered, errors, latest, completed, session, scan = payload
                    owners = self.preferences["owners"]
                    self.discovered = {owner: discovered[owner] for owner in owners if owner in discovered}
                    self.discovery_errors = {owner: errors[owner] for owner in owners if owner in errors}
                    self.latest = {source["repo"]: latest[source["repo"]] for source in self.sources()
                                   if source["repo"] in latest}
                    scan = self.scan_for_owners(scan, owners)
                    if session is self.github_session:
                        self.catalog_scan = scan
                    if session is None:
                        self.anonymous_scan = scan
                    self.history_requested.difference_update(completed)
                    now = time.time()
                    if scan is None and not (session and session.ended.is_set()):
                        self.last_check = int(now)
                    if session and session is self.github_session and session.ended.is_set():
                        self.check_again = False
                        self.sign_out_github(expired=True)
                    self.save_release_cache()
                    if self.check_again:
                        self.check_again = False
                        self.check()
                elif kind == "observe":
                    self.observe_operations()
                elif kind == "reloadSelf":
                    self.restart_store(payload)
                    if self.closed.is_set():
                        return
                elif kind == "staged":
                    self.pending["work"] = payload[0]
                    self.pending["package"] = payload
                    self.pending["progress"]["phase"] = "installing"
                    self.schedule_apply()
                elif kind == "applyPackage":
                    self.apply_if_idle()
                elif kind in ("packageProgress", "selfProgress"):
                    progress = self.pending["progress"] if kind == "packageProgress" else self.self_progress
                    phase, downloaded, total = payload
                    progress.update(phase=phase, downloaded=downloaded, total=total)
                elif kind == "failed":
                    self.operation_finished(False, payload)
            except Exception as error:
                state_changed = True
                self.notice = error_message(error)
                diagnostics.failure("worker.result_failed", error, kind=kind)
        if state_changed:
            self.publish_state()

    def marketplace(self):
        catalog = []
        for source in self.sources():
            release = self.latest.get(source["repo"], {})
            if not release.get("tag") or not release.get("isAddin"):
                continue
            identity = release.get("identity", {})
            entry = {**source, **identity}
            entry["description"] = release.get("description", "")
            entry["stars"] = release.get("stars", 0)
            entry["tag"] = release["tag"]
            entry["notes"] = release.get("notes", "")
            entry["publishedAt"] = release.get("publishedAt", "")
            entry["prerelease"] = release.get("prerelease", False)
            entry["assets"] = [{"name": asset["name"], "compatible": asset["compatible"]} for asset in release.get("assets", [])]
            entry["profileTags"] = [item["tag"] for item in versions.options(release)]
            entry["available"] = bool(releases.available(release) and not release.get("error")
                                      and entry["repo"].casefold() != self_update.REPO.casefold())
            entry["matches"] = [row["key"] for row in self.rows.values() if inventory.matches(entry, row)]
            catalog.append(entry)
        return sorted(catalog, key=lambda item: (
            -item["stars"], item["name"].casefold(), item["repo"],
        ))

    def repository_for(self, row):
        saved = self.preferences.get("installedSources", {}).get(row["key"], {})
        if row.get("sourceType") == "community_store" and saved.get("id") == row["id"]:
            repo = saved.get("repo")
            if isinstance(repo, str) and releases.REPO.fullmatch(repo):
                return repo
        return row["github"]

    def recorded_tag(self, row, release):
        saved = self.preferences.get("installedSources", {}).get(row["key"], {})
        tag = saved.get("tag")
        if (tag and saved.get("id") == row["id"] and row["sourceType"] == "community_store"
                and releases.version(tag) == releases.version(row["version"])):
            return tag
        if "history" in release and releases.version(row["version"]) is not None:
            matches = [option["tag"] for option in versions.options(release)
                       if releases.version(option["tag"]) == releases.version(row["version"])]
            if len(matches) == 1:
                return matches[0]
        return ""

    def source_for(self, row, catalog):
        repo = self.repository_for(row)
        if repo:
            candidates = [item for item in catalog if item["repo"].casefold() == repo.casefold()]
        else:
            candidates = [item for item in catalog if row["key"] in item["matches"]]
        return candidates[0] if len(candidates) == 1 else {}

    def version_plan(self, row, catalog, registered=None):
        source = self.source_for(row, catalog)
        release = self.latest.get(source.get("repo"), {})
        target = versions.target_for(self.preferences.get("targets", {}), row)
        if registered is None:
            registered = self.rows.values()
        duplicate = inventory.conflict(registered, row["folder"], row["id"], exclude=row["key"])
        plan = versions.plan_row(row, source, release, target, busy=row["key"] in self.operations,
                                 duplicate=inventory.duplicate_notice(duplicate) if duplicate else None)
        return plan, source, release

    def installed_rows(self, catalog):
        installed = []
        for row in self.rows.values():
            plan, source, release = self.version_plan(row, catalog)
            busy = row["key"] in self.operations
            if row.get("self"):
                busy = self.update_downloading or self.self_reload_pending
                source = {}
                if self.update_release and not self.update_ready:
                    plan.update(tag=self.update_release["tag"], direction="upgrade")
            update = plan["direction"] == "upgrade"
            newer_tag = versions.newer_stable_tag(row["version"], release)
            if row.get("self") and update:
                newer_tag = plan["tag"]
            installed.append({
                **row, "busy": busy, "repo": source.get("repo"),
                "installedTag": self.recorded_tag(row, release),
                "boundSource": bool(row["sourceType"] == "community_store" and self.repository_for(row)),
                "sourceVerified": bool(source.get("id") and manifest_id(source["id"]) == row["id"]),
                "releaseRepo": self.repository_for(row) or source.get("repo"), "tag": plan["tag"],
                "target": plan["target"], "direction": plan["direction"], "reason": plan["reason"],
                "duplicate": plan["duplicate"],
                "update": update, "canUpdate": update, "canRemove": not row.get("self"),
                "newerTag": newer_tag,
                "historyLoaded": "history" in release,
                "releaseError": self.release_error(release),
                "targets": [item["tag"] for item in versions.options(release)],
            })
        return installed

    @staticmethod
    def release_error(release):
        error = release.get("error", "")
        if error in ("no_releases", catalog("ru")["releases.no_releases"]):
            return Message("releases.no_releases")
        return error

    @property
    def language(self):
        return selected_language(self.preferences.get("language", SYSTEM))

    @staticmethod
    def open_folder(folder):
        if sys.platform == "win32":
            os.startfile(str(folder))
        else:
            subprocess.Popen(["open", str(folder)])

    def snapshot(self):
        catalog = self.marketplace()
        installed = self.installed_rows(catalog)
        updates = sum(row["update"] for row in installed)
        for entry in catalog:
            entry["updates"] = [{"key": row["key"], "version": row["version"], "tag": row["tag"]}
                                for row in installed if row["repo"] == entry["repo"] and row["canUpdate"]]
        installed_by_owner = {owner.casefold(): set() for owner in self.preferences["owners"]}
        for row in installed:
            repo = (row["releaseRepo"] or "").casefold()
            owner, separator, _ = repo.partition("/")
            if separator and owner in installed_by_owner:
                installed_by_owner[owner].add(repo)
        session = self.github_session
        retry_at = releases.retry_at(session)
        progress = []
        task = (self.pending or {}).get("progress")
        if task:
            finished = sum(item["status"] != "skipped" and item.get("action") != "configure"
                           for item in self.batch_results)
            progress.append({**task, "index": finished + 1 if self.batch else 1,
                             "count": self.batch_total if self.batch else 1})
        if self.self_progress:
            progress.append(self.self_progress)
        state = {"installed": installed, "catalog": catalog, "checking": self.checking, "updateCount": updates,
                "checked": self.last_check, "pending": bool(self.pending or self.batch), "notice": self.notice,
                "canOpenRemovalArchive": inventory.available_removal_archive(self.addin_root) is not None,
                "newReleaseCount": sum(bool(row["update"] or row["newerTag"]) for row in installed),
                "retryAt": retry_at if retry_at > time.time() else 0,
                "github": {"configured": bool(github_auth.CLIENT_ID.fullmatch(self.github_client_id)),
                           "connected": bool(session), "connecting": bool(self.github_attempt),
                           "profile": session.profile if session else None,
                           "profileLoading": bool(session and session.profile_loading),
                           "code": self.github_code, "error": self.github_error,
                           "copyFeedback": self.github_copy_feedback},
                "profileSaved": getattr(self, "profile_saved", False),
                "profilePreview": getattr(self, "profile_preview", None), "preview": self.preview, "batchResults": self.batch_results, "progress": progress,
                "storeVersion": self.version, "storeUpdateNotice": self.update_notice,
                "storeUpdateDisabled": self.update_disabled,
                "settings": {**self.preferences, "trustedOwners": self.trusted_owners},
                "discoveryErrors": self.discovery_errors,
                "ownerCounts": {owner: len(repos) for owner, repos in installed_by_owner.items()},
                "ownerAvatars": {owner: next((item["ownerAvatar"] for item in self.discovered.get(owner, [])
                                                if item.get("ownerAvatar")), "")
                                 for owner in self.preferences["owners"]}}
        language = self.language
        return {**translate(state, language), "language": language, "translations": palette_catalog(language)}

    def open_report(self, data):
        kind = data.get("kind")
        if kind == "security":
            url = reporting.security_policy_url(self.language)
        elif kind == "source":
            owner = next((owner for owner in self.preferences["owners"] if owner == data.get("owner")), None)
            if owner is None:
                raise ValueError(Message("runtime.report_unavailable"))
            url = reporting.issue_url(kind, self.language, self.version, owner=owner)
        elif kind == "addin":
            repo = data.get("repo")
            entry = next((entry for entry in self.marketplace() if entry["repo"] == repo), None) if repo is not None else None
            if repo is not None and entry is None:
                raise ValueError(Message("runtime.report_unavailable"))
            url = reporting.issue_url(kind, self.language, self.version, entry=entry)
        else:
            raise ValueError(Message("runtime.report_unavailable"))
        try:
            opened = webbrowser.open(url)
        except (OSError, webbrowser.Error):
            opened = False
        self.notice = "" if opened else Message("runtime.report_browser_failed")

    def dispatch(self, action, data):
        if action == "state":
            self.place_palette(data.get("display"))
            self.refresh_inventory()
            return
        if action == "openRemovalArchive":
            directory = inventory.available_removal_archive(self.addin_root)
            if directory is not None:
                self.open_folder(directory)
            return
        if action == "report":
            self.open_report(data)
            return
        if action == "openUrl":
            url = data.get("url")
            if isinstance(url, str) and len(url) <= 2048 and not re.search(r"[\x00-\x20\\\\]", url):
                parsed = urlsplit(url)
                if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password:
                    webbrowser.open(url)
            return
        if action == "language":
            preference = data.get("language")
            if preference not in (SYSTEM, *LANGUAGES):
                raise ValueError(Message("runtime.language_invalid"))
            self.set_preferences({"language": preference})
            return
        if action == "dismissGuide":
            if self.preferences.get("guideSeen") is not True:
                self.set_preferences({"guideSeen": True})
            return
        if action == "resetGuide":
            self.notice = ""
            self.set_preferences({"guideSeen": False})
            return
        if action == "githubSignIn":
            self.sign_in_github()
            return
        if action == "githubProfile":
            self.load_github_profile()
            return
        if action == "githubCopyCode":
            self.copy_github_code()
            return
        if action == "githubOpenDevice":
            if self.github_attempt and self.github_code:
                try:
                    opened = webbrowser.open(github_auth.DEVICE_PAGE)
                except (OSError, webbrowser.Error):
                    opened = False
                self.github_error = "" if opened else Message("github.browser_failed")
            return
        if action == "githubCancel":
            if self.github_attempt:
                self.github_attempt.set()
                self.github_attempt = None
                self.github_code = ""
                self.github_error = ""
                self.github_copy_feedback = ""
            return
        if action == "githubSignOut":
            self.sign_out_github()
            return
        self.notice = ""
        if action == "exportLogs":
            self.export_logs()
            return
        if action == "check":
            self.refresh_inventory()
            self.check()
            return
        if action == "native":
            command = self.ui.commandDefinitions.itemById("ScriptsManagerCommand")
            if command:
                command.execute()
            return
        if action == "preferences":
            automatic, interval = data.get("automatic"), data.get("interval")
            if not isinstance(automatic, bool) or type(interval) is not int or interval not in CHECK_INTERVALS:
                raise ValueError(Message("runtime.interval_invalid"))
            self.set_preferences({"automatic": automatic, "interval": interval})
            self.schedule_changed.set()
            return
        if action in ("addOwner", "removeOwner"):
            owner = github_owner(str(data.get("url", "")))
            subscriptions = self.preferences["ownerSubscriptions"]
            if any(name.casefold() == owner.casefold() for name in self.trusted_owners):
                return
            if action == "addOwner":
                updated = trusted_stores.merge_owners(subscriptions, [owner])
            else:
                updated = [name for name in subscriptions if name.casefold() != owner.casefold()]
            if updated != subscriptions:
                self.set_preferences({"ownerSubscriptions": updated,
                                      "owners": trusted_stores.merge_owners(self.trusted_owners, updated)})
                if action == "removeOwner":
                    self.prune_sources()
                    self.save_release_cache()
            self.queue_check()
            return
        self.refresh_inventory()
        row = self.rows.get(data.get("key"))
        if action == "release":
            source = self.source_for(row, self.marketplace()) if row else {}
            repo = (self.repository_for(row) or source.get("repo")) if row else data.get("repo")
            if repo and (row or any(item["repo"] == repo for item in self.sources())):
                releases.validate_repo(repo)
                latest = self.latest.get(repo, {})
                target = versions.target_for(self.preferences.get("targets", {}), row) if row else versions.LATEST
                tag = (target if target != versions.LATEST else latest.get("tag")) if row else None
                suffix = "/tag/" + quote(tag, safe="") if tag else ""
                webbrowser.open(f"https://github.com/{repo}/releases{suffix}")
            return
        if action == "openFolder" and row:
            self.open_folder(row["folder"])
            return
        if self.pending or self.batch:
            raise RuntimeError(Message("runtime.wait_install"))
        if action == "exportProfile":
            self.export_profile(data)
        elif action == "importProfile":
            self.import_profile()
        elif action == "applyProfile":
            self.apply_profile(data)
        elif action == "selfUpdate" and row and row.get("self"):
            self.prepare_self_update(data.get("tag"))
        elif action == "history" and row:
            source = self.source_for(row, self.marketplace())
            if not source:
                raise ValueError(Message("runtime.catalog_repo_missing_now"))
            self.history_requested.add(source["repo"])
            self.queue_check()
        elif action == "target" and row and not row.get("self"):
            self.save_target(row, data.get("target"))
        elif action == "registerLocal":
            self.register_local()
        elif action == "preview":
            mode = data.get("mode")
            if mode not in ("all", "selected", "single"):
                raise ValueError(Message("runtime.choose_addins"))
            keys = data.get("keys", [])
            if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
                raise ValueError(Message("runtime.choose_addins"))
            if mode == "single" and len(keys) != 1:
                raise ValueError(Message("runtime.choose_one_addin"))
            plan = self.batch_plan(mode, keys)
            self.preview = {"token": uuid4().hex, "mode": mode, "keys": keys, "rows": plan}
        elif action == "applyBatch":
            self.apply_batch(data)
        elif action == "startup" and row and isinstance(data.get("enabled"), bool):
            if not self.is_idle():
                raise RuntimeError(Message("runtime.close_command"))
            script = self.scripts_by_key[row["key"]]
            script.isRunOnStartup = data["enabled"]
            if bool(script.isRunOnStartup) != data["enabled"]:
                raise RuntimeError(Message("runtime.startup_failed"))
            self.refresh_inventory()
        elif action == "remove" and row and not row.get("self") and data.get("confirmed") is True:
            self.remove(row)
        elif action == "reload" and row:
            self.reload_addin(row)
        elif action in ("enable", "disable") and row and not row.get("self"):
            enable = action == "enable"
            if row["key"] in self.operations or row["running"] is enable:
                return
            if not self.is_idle():
                raise RuntimeError(Message("runtime.close_command"))
            self.lifecycle(row, enable)
            self.observe(row, enable)
        elif action == "install":
            self.prepare_install(data)
        else:
            raise ValueError(Message("runtime.action_unavailable"))

    def set_preferences(self, changes):
        preferences = {**self.preferences, **changes}
        self.write_data("settings.json", preferences)
        self.preferences = preferences

    def export_profile(self, doc):
        self.profile_saved = False
        self.notice = ""
        profiles.validate(doc)
        profiles.require(len(json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")) <= profiles.MAX_BYTES)
        picker = self.ui.createFileDialog()
        picker.title = catalog(self.language)["ui.profile_export"]
        picker.filter = "Community Store (*.fusion-profile.json)"
        picker.initialFilename = "CommunityStore.fusion-profile.json"
        if picker.showSave() == adsk.core.DialogResults.DialogOK:
            bootstrap.write_json(Path(picker.filename), doc, indent=2)
            self.profile_saved = True
            self.notice = Message("profile.saved")

    def import_profile(self):
        picker = self.ui.createFileDialog()
        picker.title = catalog(self.language)["ui.profile_import"]
        picker.filter = "Community Store (*.json)"
        if picker.showOpen() != adsk.core.DialogResults.DialogOK:
            return
        self.profile_import = self.profile_preview = None
        doc = profiles.read(Path(picker.filename))
        token = uuid4().hex
        self.profile_import = {"token": token, "doc": doc, "metadata": {}}
        self.profile_preview = None
        if not doc.get("addins"):
            self.profile_preview = self.profile_plan()
            return
        self.notice = Message("profile.resolving")
        session = self.github_session

        @releases.use_session(session)
        def worker():
            metadata = {}
            for item in doc["addins"]:
                if self.closed.is_set() or not self.profile_import or self.profile_import["token"] != token:
                    return
                repo = item["repo"]
                try:
                    metadata[repo] = releases.latest({"repo": repo, "name": repo.split("/")[1]},
                                                     with_history=True, inspect_package=False)
                except releases.RateLimited:
                    deadline = releases.retry_at(session)
                    reason = (Message("ui.rate_limit_retry_at", time=datetime.fromtimestamp(deadline).strftime("%H:%M"))
                              if deadline else Message("github.request_failed"))
                    for remaining in doc["addins"]:
                        metadata.setdefault(remaining["repo"], {"error": True, "problem": reason})
                    break
                except Exception as error:
                    metadata[repo] = {"error": True, "problem": error_message(error)}
            self.complete("profileMetadata", (token, metadata))
        threading.Thread(target=worker, daemon=True).start()

    def profile_plan(self):
        request = self.profile_import
        doc = request["doc"]
        owners = {owner.casefold() for owner in self.preferences["owners"]}
        registered = self.registration_rows() if doc.get("addins") else []
        rows = []
        for item in doc.get("addins", []):
            entry = request["metadata"][item["repo"]]
            line = profiles.plan_item(item, entry, registered, self.preferences)
            name = entry.get("identity", {}).get("addinName")
            if line["action"] == "install" and name and Path(name).name == name:
                folder = self.addin_root / name
                if folder.exists() or folder.is_symlink():
                    line.update(action="skip", reason=Message("runtime.folder_used", folder=folder))
            rows.append(line)
        return {
            "token": request["token"], "name": doc["name"],
            "settings": {key: {"from": self.preferences[key], "to": value}
                         for key, value in doc.get("settings", {}).items() if value != self.preferences[key]},
            "owners": [owner for owner in trusted_stores.merge_owners(doc.get("ownerSubscriptions", []))
                       if owner.casefold() not in owners],
            "manual": doc.get("manual", []),
            "rows": rows,
        }

    def apply_profile(self, data):
        plan = self.profile_preview
        if not plan or plan["token"] != data.get("token"):
            raise ValueError(Message("runtime.preview_outdated"))
        current = self.profile_plan()
        if current != plan:
            self.profile_import["token"] = current["token"] = uuid4().hex
            self.profile_preview = current
            raise ValueError(Message("runtime.preview_outdated"))
        if not self.is_idle() or self.operations:
            raise RuntimeError(Message("runtime.close_command"))
        picks = data.get("selected", {})
        profiles.require(isinstance(picks, dict))
        request = self.profile_import
        jobs, configure, results = [], [], []
        for item, line in zip(request["doc"].get("addins", []), plan["rows"]):
            repo = item["repo"]
            if line["action"] == "skip" or repo not in picks:
                results.append({**line, "status": "skipped", "message": line.get("reason", "")})
                continue
            row = self.rows.get(line["key"])
            if line["key"] and not row:
                raise ValueError(Message("runtime.preview_outdated"))
            if line["action"] == "configure":
                configure.append((item, line, row))
                continue
            entry = request["metadata"][repo]
            release = versions.select_release(entry, item["installTarget"])
            asset = versions.select_asset(release, picks[repo])
            if not asset:
                raise ValueError(Message("runtime.choose_addin_zip", name=repo))
            source = {"repo": repo, "id": manifest_id(item["manifestId"]),
                      "name": entry.get("identity", {}).get("addinName") or repo.split("/")[1]}
            jobs.append((row, source, {**release, "asset": asset, "profileMetadata": entry},
                         {**line, "profileTarget": item["updateTarget"]}))
        changes = {key: change["to"] for key, change in plan["settings"].items()}
        if plan["owners"]:
            subscriptions = trusted_stores.merge_owners(self.preferences["ownerSubscriptions"], plan["owners"])
            changes.update(ownerSubscriptions=subscriptions, owners=trusted_stores.merge_owners(self.trusted_owners, subscriptions))
        if changes:
            self.set_preferences(changes)
            self.schedule_changed.set()
        self.profile_import = self.profile_preview = None
        for item, line, row in configure:
            try:
                script = self.scripts_by_key[row["key"]]
                if "runOnStartup" in item:
                    script.isRunOnStartup = item["runOnStartup"]
                    if bool(script.isRunOnStartup) != item["runOnStartup"]:
                        raise RuntimeError(Message("runtime.startup_failed"))
                targets = dict(self.preferences.get("targets", {}))
                targets[row["key"]] = {"id": row["id"], "target": item["updateTarget"]}
                self.set_preferences({"targets": targets})
                results.append({**line, "status": "completed", "message": ""})
            except Exception as error:
                results.append({**line, "status": "failed", "message": error_message(error)})
        self.refresh_inventory()
        self.batch_results = results
        self.batch_total = len(jobs)
        self.notice = Message("profile.applied")
        if jobs:
            self.batch = iter(jobs)
            self.start_next_update()
        if plan["owners"]:
            self.queue_check()

    def export_logs(self):
        try:
            picker = self.ui.createFileDialog()
            strings = catalog(self.language)
            picker.title = strings["ui.export_logs"]
            picker.filter = strings["runtime.logs_zip_filter"]
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
            picker.initialFilename = f"CommunityStore-logs-{stamp}.zip"
            if picker.showSave() != adsk.core.DialogResults.DialogOK:
                return
            diagnostics.export(Path(picker.filename))
        except Exception as error:
            diagnostics.failure("logs.export_failed", error)
            self.notice = Message("runtime.logs_export_failed")
        else:
            diagnostics.event("logs.export_finished")
            self.notice = Message("runtime.logs_exported")

    def remove(self, row):
        if row["key"] in self.operations or not self.is_idle():
            raise RuntimeError(Message("runtime.remove_busy"))

        def finish(stopped):
            if not stopped:
                self.notice = Message("runtime.stop_unconfirmed")
                return
            script = self.scripts_by_key[row["key"]]
            folder = inventory.archive_candidate(row, self.addin_root)
            target = None
            try:
                if folder:
                    archive = inventory.removal_archive_path(folder.parent)
                    archive.mkdir(exist_ok=True)
                    target = folder.rename(archive / f'{folder.name}-{uuid4().hex[:8]}')
                if row["location"] == adsk.core.ScriptSourceLocations.LinkedScriptSourceLocation:
                    if not script.unlink():
                        raise RuntimeError(Message("runtime.unlink_failed"))
                else:
                    script.isVisible = False
                    if script.isVisible:
                        raise RuntimeError(Message("runtime.hide_failed"))
            except Exception:
                if target and (target.exists() or target.is_symlink()):
                    target.rename(folder)
                if row["running"]:
                    self.lifecycle(row, True)
                raise
            self.refresh_inventory()

        if row["running"] is False:
            finish(True)
        else:
            self.lifecycle(row, False)
            self.observe(row, False, finish)

    def save_target(self, row, target):
        _, source, release = self.version_plan(row, self.marketplace())
        if target != versions.LATEST and (
                not source or not versions.select_release(release, target)):
            raise ValueError(Message("runtime.choose_target"))
        targets = {**self.preferences.get("targets", {})}
        if target == versions.LATEST:
            targets.pop(row["key"], None)
        else:
            targets[row["key"]] = {"id": row["id"], "target": target}
        self.set_preferences({"targets": targets})

    def registration_rows(self):
        rows, _ = inventory.from_fusion(self.app.scripts, self.addin_root, include_hidden=True)
        return rows

    def registered_conflict(self, folder, identity, *, exclude=None):
        return inventory.conflict(self.registration_rows(), folder, identity, exclude=exclude)

    def register_local(self):
        if not self.is_idle():
            raise RuntimeError(Message("runtime.close_command"))
        picker = self.ui.createFolderDialog()
        picker.title = catalog(self.language)["runtime.register_local"]
        if picker.showDialog() != adsk.core.DialogResults.DialogOK:
            return
        folder = Path(os.path.abspath(picker.folder))
        identity = inventory.local_addin(folder)
        duplicate = self.registered_conflict(folder, identity)
        if duplicate:
            raise ValueError(inventory.duplicate_notice(duplicate))
        script = self.app.scripts.addExisting(str(folder))
        if (not script or not script.isAddIn or not script.isVisible
                or inventory.canonical_path(script.folder) != inventory.canonical_path(folder)):
            raise RuntimeError(Message("runtime.register_local_failed"))
        self.refresh_inventory()
        if os.path.normcase(os.path.abspath(script.folder)) not in self.rows:
            raise RuntimeError(Message("runtime.register_local_failed"))
        self.prefetch_installed_histories(inventory_changed=True)

    def batch_plan(self, mode, keys):
        catalog = self.marketplace()
        registered = self.registration_rows()
        rows = list(self.rows.values()) if mode == "all" else [self.rows.get(key) for key in dict.fromkeys(keys)]
        plan = []
        for row in rows:
            if not row:
                raise ValueError(Message("runtime.registration_changed"))
            item, _, _ = self.version_plan(row, catalog, registered)
            if mode != "single" and item["direction"] == "downgrade":
                item["direction"] = ""
                item["reason"] = Message("runtime.downgrade_confirmation")
                item.pop("assets")
            plan.append(item)
        return plan

    def apply_batch(self, data):
        preview = self.preview
        self.preview = None
        if (not preview or data.get("token") != preview["token"]
                or preview["rows"] != self.batch_plan(preview["mode"], preview["keys"])):
            raise ValueError(Message("runtime.preview_outdated"))
        selections = data.get("assets", {})
        if not isinstance(selections, dict):
            raise ValueError(Message("runtime.choose_batch_zips"))
        catalog = self.marketplace()
        jobs, results = [], []
        for item in preview["rows"]:
            if item["reason"]:
                results.append({**item, "status": "skipped", "message": item["reason"]})
                continue
            row = self.rows[item["key"]]
            _, source, entry = self.version_plan(row, catalog)
            release = versions.select_release(entry, item["target"])
            asset = versions.select_asset(release, selections.get(item["key"]))
            if not asset:
                raise ValueError(Message("runtime.choose_addin_zip", name=row["name"]))
            jobs.append((row, source, {**release, "asset": asset}, item))
        self.batch_results = results
        self.batch_total = len(jobs)
        diagnostics.event("addin.batch_started", jobs=len(jobs), skipped=len(results))
        if jobs:
            self.batch = iter(jobs)
            self.start_next_update()

    def start_next_update(self):
        job = next(self.batch, None)
        if job is None:
            self.batch = None
            return
        row, source, release, item = job
        self.stage_package(row, source, release, item)

    def operation_finished(self, success, message):
        task, self.pending = self.pending, None
        diagnostics.event("addin.package_finished", success=success, reason=diagnostics.message_key(message))
        self.notice = message if not success and not task["item"] else ""
        if task["item"]:
            self.batch_results.append({**task["item"], "status": "completed" if success else "failed",
                                       "message": message})
        self.refresh_inventory()
        if self.batch and not self.closed.is_set():
            self.start_next_update()
        elif success:
            self.prefetch_installed_histories(inventory_changed=True)

    def prepare_self_update(self, tag):
        release = self.update_release
        if (self.update_disabled or self.update_ready or self.update_downloading
                or not release or release["tag"] != tag):
            raise ValueError(Message("runtime.compatible_release_unavailable"))
        self.update_downloading = True
        diagnostics.event("store.update_started", target=diagnostics.version(tag))
        self.update_notice = ""
        self.self_progress = {"name": DISPLAY_NAME, "tag": tag, "phase": "downloading",
                              "downloaded": 0, "total": 0, "index": 1, "count": 1}

        def worker():
            try:
                notice = self_update.stage(self.root, release, self.closed,
                                           progress=lambda *value: self.complete("selfProgress", value))
                diagnostics.event("store.update_staged", target=diagnostics.version(tag))
                self.complete("selfStaged", (notice, ""))
            except Exception as error:
                diagnostics.failure("store.update_stage_failed", error)
                self.complete("selfStaged", ("", error_message(error)))

        threading.Thread(target=worker, daemon=True).start()

    def prepare_install(self, data):
        source = next((item for item in self.marketplace() if item["repo"] == data.get("repo")), None)
        if not source or not source["available"]:
            raise ValueError(Message("runtime.compatible_release_unavailable"))
        if source.get("id") or source.get("addinName"):
            expected = self.addin_root / source.get("addinName", source["name"])
            duplicate = self.registered_conflict(expected, source.get("id"))
            if duplicate:
                raise ValueError(inventory.duplicate_notice(duplicate))
        if source["matches"]:
            raise ValueError(inventory.duplicate_notice(self.rows[source["matches"][0]]))
        release = self.latest[source["repo"]]
        asset = versions.select_asset(release, data.get("asset"))
        if not asset:
            raise ValueError(Message("runtime.select_zip"))
        self.stage_package(None, source, {**release, "asset": asset}, None)

    def stage_package(self, row, source, release, item):
        source = {**source, "name": source.get("addinName", source["name"])}
        if row and row["id"]:
            source["id"] = row["id"]
        # A Git origin or a previous Store install already binds this row to the release repository.
        allow_missing_id = bool(row and not source.get("id")
                                and row["name"].casefold() == source["name"].casefold()
                                and self.repository_for(row).casefold() == source["repo"].casefold())
        self.pending = {"row": row, "item": item, "profileMetadata": release.get("profileMetadata"),
                        "repository": source["repo"],
                        "progress": {"name": row["name"] if row else source["name"], "tag": release["tag"],
                                     "phase": "downloading", "downloaded": 0, "total": 0}}
        self.notice = ""
        diagnostics.event("addin.package_started", updating=row is not None,
                          target=diagnostics.version(release["tag"]))

        def worker():
            try:
                package = releases.stage(source, release, self.root / ".community-store-staging",
                                         progress=lambda *value: self.complete("packageProgress", value),
                                         allow_missing_id=allow_missing_id)
                if self.closed.is_set():
                    shutil.rmtree(package[0], ignore_errors=True)
                else:
                    diagnostics.event("addin.package_staged")
                    self.complete("staged", package)
            except Exception as error:
                diagnostics.failure("addin.package_stage_failed", error)
                self.complete("failed", error_message(error))
        threading.Thread(target=worker, daemon=True).start()

    def is_idle(self):
        return self.ui.activeCommand in ("", "SelectCommand", COMMAND_ID)

    def schedule_apply(self, delay=0.15):
        timer = threading.Timer(delay, self.complete, args=("applyPackage", None))
        timer.daemon = True
        timer.start()

    def install_phase(self, phase):
        diagnostics.event("addin.package_phase", phase=phase)
        self.pending["progress"]["phase"] = phase
        self.publish_state()

    def apply_if_idle(self):
        if not self.pending or not self.pending.get("package") or self.pending.get("applying"):
            return
        if not self.is_idle():
            if self.pending["progress"]["phase"] != "waiting":
                diagnostics.event("addin.package_phase", phase="waiting")
            self.pending["progress"]["phase"] = "waiting"
            self.schedule_apply(1)
            return
        task = self.pending
        task["applying"] = True
        work, package, filename, info = task["package"]
        package_id = manifest_id(info.get("id"))
        old = task["row"]
        backup = None
        target = self.addin_root / package.name
        old_folder = Path(old["folder"]) if old else None
        moving = old_folder is not None and os.path.normcase(str(old_folder)) != os.path.normcase(str(target))
        if old_folder is not None and not moving:
            # Preserve the path spelling recorded by Fusion.
            target = old_folder
        old_registration = None
        old_hidden = False
        installed = False
        live = None
        new = {"entry": str(target / filename), "key": os.path.normcase(str(target.absolute())),
               "folder": str(target), "name": target.name}
        registered = None
        from_folder = bool(old and old.get("sourceType") == "folder")

        def rollback(reason):
            replacement_present = installed
            try:
                if registered and registered.isValid and (not old or moving):
                    registered.isVisible = False
                if installed:
                    target.rename(work / "failed-package")
                    replacement_present = False
                if backup and backup.exists():
                    backup.rename(old_folder)
                if old_hidden or (old and installed):
                    restored = self.app.scripts.itemByPath(str(old_folder)) or self.app.scripts.addExisting(str(old_folder))
                    if not restored:
                        raise RuntimeError(Message("runtime.restore_registration"))
                    restored.isVisible = True
                    restored.isRunOnStartup = live["startup"]
                    if not restored.isVisible or bool(restored.isRunOnStartup) != live["startup"]:
                        raise RuntimeError(Message("runtime.restore_startup"))
                shutil.rmtree(work, ignore_errors=True)
                if live and live["running"]:
                    self.refresh_inventory()
                    self.lifecycle(live, True)
                    self.observe(live, True, lambda resumed: self.operation_finished(
                        False, reason if resumed else Message("runtime.previous_restart_unconfirmed", reason=reason)))
                    return
            except Exception as error:
                diagnostics.failure("addin.rollback_failed", error)
                if replacement_present and registered and registered.isValid:
                    try:
                        registered.isRunOnStartup = False
                        if registered.isRunOnStartup:
                            raise RuntimeError(Message("runtime.restore_startup"))
                    except Exception as startup_error:
                        diagnostics.failure("addin.rollback_disable_startup_failed", startup_error)
                reason = Message("runtime.rollback_failed", reason=reason, error=error_message(error), backup=backup or work)
            self.operation_finished(False, reason)

        def fail(reason):
            self.install_phase("restoring")
            if installed:
                try:
                    self.refresh_inventory()
                    if self.rows.get(new["key"], {}).get("running"):
                        self.lifecycle(new, False)
                        self.observe(new, False, lambda stopped: rollback(reason) if stopped else
                                     self.operation_finished(False, Message("runtime.new_stop_unconfirmed", reason=reason, backup=backup or work)))
                        return
                except Exception as error:
                    diagnostics.failure("addin.rollback_stop_failed", error)
                    self.operation_finished(False, Message("runtime.new_stop_failed", reason=reason, error=error_message(error), backup=backup or work))
                    return
            rollback(reason)

        def finish_success(started):
            if not started:
                fail(Message("runtime.start_unconfirmed"))
                return
            try:
                sources = {**self.preferences.get("installedSources", {})}
                binding = {"id": package_id, "repo": task["repository"]}
                if tag := task["progress"].get("tag"):
                    binding["tag"] = tag
                if ((task["item"] or {}).get("profileTarget")
                        or (old and sources.get(old["key"], {}).get("profileInstalled"))):
                    binding["profileInstalled"] = True
                sources[new["key"]] = binding
                changes = {"installedSources": sources}
                if moving:
                    sources.pop(old["key"], None)
                    targets = {**self.preferences.get("targets", {})}
                    saved = targets.pop(old["key"], None)
                    if saved and saved.get("id") == old["id"]:
                        targets[new["key"]] = saved
                        changes["targets"] = targets
                if task["item"] and "profileTarget" in task["item"]:
                    targets = dict(changes.get("targets", self.preferences.get("targets", {})))
                    targets[new["key"]] = {"id": package_id, "target": task["item"]["profileTarget"]}
                    changes["targets"] = targets
                self.set_preferences(changes)
            except Exception as error:
                diagnostics.failure("addin.package_save_settings_failed", error)
                fail(error_message(error))
                return
            if task.get("profileMetadata"):
                entry = task["profileMetadata"]
                identity = entry.get("identity") or releases.manifest_identity(info, package.name)
                self.latest[task["repository"]] = {**entry, "isAddin": True, "identity": identity}
            shutil.rmtree(work, ignore_errors=True)
            self.operation_finished(True, Message("runtime.installed", name=target.name, version=info["version"]))

        def replace(stopped):
            nonlocal backup, old_hidden, registered, installed
            try:
                if not stopped:
                    raise RuntimeError(Message("runtime.previous_stop_unconfirmed"))
                self.install_phase("installing")
                self.refresh_inventory()
                if live and (self.rows.get(live["key"], {}).get("version") != live["version"]
                             or self.rows[live["key"]]["sourceType"] != live["sourceType"]
                             or (not from_folder and not self.rows[live["key"]]["writable"])):
                    raise ValueError(Message("runtime.installation_changed_stopped"))
                assert_no_conflict()
                if live:
                    if moving:
                        old_hidden = True
                        if from_folder and live["location"] == inventory.LINKED_LOCATION:
                            if not old_registration.unlink():
                                raise RuntimeError(Message("runtime.unlink_failed"))
                        else:
                            old_registration.isVisible = False
                            if old_registration.isVisible:
                                raise RuntimeError(Message("runtime.unregister_failed"))
                    if not from_folder:
                        backup_root = old_folder.parent / ".community-store-backups"
                        if bootstrap.redirected_directory(backup_root):
                            raise ValueError(Message("runtime.backup_root_linked"))
                        backup_root.mkdir(exist_ok=True)
                        backup = old_folder.rename(backup_root / f"{package.name}-backup-{uuid4().hex}")
                self.addin_root.mkdir(parents=True, exist_ok=True)
                package.rename(target)
                installed = True
                script = self.app.scripts.itemByPath(str(target)) or self.app.scripts.addExisting(str(target))
                if not script or inventory.canonical_path(script.folder) != inventory.canonical_path(target):
                    raise RuntimeError(Message("runtime.register_failed"))
                registered = script
                registered.isVisible = True
                startup = (task["item"] or {}).get("startup", live["startup"] if live else True)
                registered.isRunOnStartup = startup
                if not registered.isVisible or bool(registered.isRunOnStartup) != startup:
                    raise RuntimeError(Message("runtime.register_settings_failed"))
                self.refresh_inventory()
                if not live or live["running"]:
                    self.install_phase("starting")
                    self.lifecycle(new, True)
                    self.observe(new, True, finish_success)
                else:
                    finish_success(True)
            except Exception as error:
                diagnostics.failure("addin.package_replace_failed", error)
                fail(error_message(error))

        def assert_no_conflict():
            duplicate = self.registered_conflict(target, package_id, exclude=old["key"] if old else None)
            if duplicate:
                raise ValueError(inventory.duplicate_notice(duplicate))
            if (moving or from_folder or not old) and (target.exists() or target.is_symlink()):
                raise ValueError(Message("runtime.folder_used", folder=target))

        try:
            self.refresh_inventory()
            if bootstrap.redirected_directory(self.addin_root):
                raise ValueError(Message("runtime.managed_root_linked"))
            assert_no_conflict()
            if old:
                current = self.rows.get(old["key"])
                if (not current or current["sourceType"] != old["sourceType"]
                        or (not from_folder and not current["writable"]) or current["version"] != old["version"]
                        or current["id"] != old["id"] or current["canonical"] != old["canonical"]):
                    raise ValueError(Message("runtime.installation_changed"))
                if not from_folder and (
                    os.path.normcase(target.name) != os.path.normcase(old_folder.name)
                    or os.path.normcase(filename) != os.path.normcase(Path(current["entry"]).name)
                ):
                    raise ValueError(Message("runtime.package_renamed"))
                if self.addin_root.is_relative_to(old_folder):
                    raise ValueError(Message("runtime.package_contains_store"))
                if current["id"] and current["id"] != package_id:
                    raise ValueError(Message("runtime.package_id_changed"))
                if not package_id and self.repository_for(current).casefold() != task["repository"].casefold():
                    raise ValueError(Message("runtime.installation_changed"))
                live = current
                old_registration = self.scripts_by_key[live["key"]]
                if live["running"]:
                    self.install_phase("stopping")
                    self.lifecycle(live, False)
                    self.observe(live, False, replace)
                    return
            replace(True)
        except Exception as error:
            diagnostics.failure("addin.package_apply_failed", error)
            fail(error_message(error))


def run(context, root, version, update_notice, update_disabled):
    global _controller
    if _controller:
        return
    diagnostics.start()
    _controller = Store(root, version, update_notice, update_disabled)
    _controller.start()
    if not isinstance(context, dict) or not context.get("IsApplicationStartup"):
        _controller.show()
    diagnostics.event("store.started")


def stop(context):
    global _controller
    if _controller:
        controller, _controller = _controller, None
        controller.dispose()
