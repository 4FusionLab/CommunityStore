"""Select and recover a Community Store runtime without changing Fusion's entry files."""
import importlib
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import ModuleType
from uuid import uuid4

from . import diagnostics
from .localization import Message

UPDATE_DIR = ".community-store-updates"
PRODUCT_ID = "08b17a72-c1c4-4c56-b7b0-eeddf157ee43"
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
SLOT = re.compile(r"([0-9]+\.[0-9]+\.[0-9]+)-[0-9a-f]{32}")
REQUIRED_RUNTIME = ("runtime.py", "bootstrap.py", "self_update.py", "config.py",
                    "core.py", "inventory.py", "releases.py", "ui/index.html",
                    "ui/store.js", "ui/store.css")


def update_disabled(root):
    if root.absolute() != root.resolve():
        return Message("bootstrap.linked_folder")
    for parent in (root, *root.parents):
        marker = parent / ".git"
        if marker.exists() or marker.is_symlink():
            return Message("bootstrap.git_checkout")
    return ""


def version_key(value):
    match = VERSION.fullmatch(value)
    return tuple(map(int, match.groups())) if match else (0, 0, 0)


def read_manifest(directory):
    return json.loads((directory / "CommunityStore.manifest").read_text(encoding="utf-8-sig"))


def slot_version(slot):
    return SLOT.fullmatch(slot)[1]


def state_dir(root):
    directory = root / UPDATE_DIR
    if redirected_directory(directory):
        raise OSError("Community Store update directory is a link")
    return directory


def redirected_directory(path):
    return path.is_symlink() or path.resolve() != path.absolute()


def read_state(directory):
    try:
        data = json.loads((directory / "state.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError("Invalid Community Store update state")
    for key in ("active", "previous", "pending", "trying"):
        value = data.get(key)
        if value is not None and (not isinstance(value, str) or not SLOT.fullmatch(value)):
            raise ValueError("Invalid Community Store update slot")
    rejected = data.get("rejected", [])
    if not isinstance(rejected, list) or any(not isinstance(v, str) or not VERSION.fullmatch(v) for v in rejected):
        raise ValueError("Invalid Community Store rollback state")
    return data


def save_state(directory, state):
    write_json(directory / "state.json", state)


def write_json(path, data, *, indent=None):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(data, stream, ensure_ascii=False, indent=indent)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        # Ordinary Windows read handles can briefly block replacement.
        for delay in (0.025, 0.05, 0.1, 0.2, None):
            try:
                os.replace(temporary, path)
                break
            except OSError as error:
                if delay is None or not (windows_lock_error(error) or getattr(error, "winerror", None) == 5):
                    raise
                time.sleep(delay)
    finally:
        temporary.unlink(missing_ok=True)


def windows_lock_error(error):
    return getattr(error, "winerror", None) in (32, 33)


@contextmanager
def update_lock(directory):
    """Serialize state transitions. The OS releases the lock if Fusion exits."""
    directory.mkdir(exist_ok=True)
    with (directory / "state.lock").open("a+b") as stream:
        if sys.platform == "win32":
            import msvcrt

            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield


def validate_slot(directory, expected_version):
    info = read_manifest(directory)
    if (not isinstance(info, dict) or info.get("version") != expected_version
            or str(info.get("id", "")).lower() != PRODUCT_ID
            or info.get("type") != "addin" or info.get("autodeskProduct") != "Fusion360"):
        raise ValueError("Invalid Community Store runtime manifest")
    if any(not (directory / path).is_file() for path in REQUIRED_RUNTIME):
        raise ValueError("Incomplete Community Store runtime")


def start_runtime(code, root, version, notice, context, disabled):
    # Fusion keeps the entry module loaded. Give each attempt a fresh package
    # so a failed import cannot leak relative imports into the rollback.
    name = "_community_store_" + uuid4().hex
    package = ModuleType(name)
    package.__path__ = [str(code)]
    sys.modules[name] = package
    runtime = None
    try:
        diagnostics.event("loader.start", version=diagnostics.version(version), staged=code.parent.name == UPDATE_DIR)
        runtime = importlib.import_module(".runtime", name)
        runtime.run(context, root, version, notice, disabled)
        return runtime
    except BaseException as error:
        diagnostics.failure("loader.start_failed", error, version=diagnostics.version(version))
        if runtime is not None:
            try:
                runtime.stop(context)
            except Exception as cleanup_error:
                diagnostics.failure("loader.cleanup_failed", cleanup_error)
        unload_package(name)
        raise


def unload_package(name):
    for module in list(sys.modules):
        if module == name or module.startswith(name + "."):
            sys.modules.pop(module, None)


def stop_runtime(runtime, context):
    try:
        runtime.stop(context)
    finally:
        unload_package(runtime.__package__)


def reject(state, slot):
    version = slot_version(slot)
    diagnostics.event("loader.rollback", version=diagnostics.version(version))
    state["rejected"] = sorted({*state.get("rejected", []), version})
    if state.get("pending") == slot:
        state["pending"] = None
    if state.get("active") == slot:
        state["active"], state["previous"] = state.get("previous"), None
    state["trying"] = None
    return Message("bootstrap.rollback", version=version)


def select_runtime(directory, root, base_version, context):
    state = read_state(directory)
    notice = ""
    if state.get("trying"):
        notice = reject(state, state["trying"])
        save_state(directory, state)
    startup = isinstance(context, dict) and context.get("IsApplicationStartup")
    pending = state.get("pending")
    if pending and version_key(slot_version(pending)) <= version_key(base_version):
        state["pending"] = None
        save_state(directory, state)
        pending = None
    candidate = pending if startup and pending else state.get("active")
    while candidate and version_key(slot_version(candidate)) > version_key(base_version):
        version = slot_version(candidate)
        before_attempt = state.copy()
        state["trying"] = candidate
        save_state(directory, state)
        try:
            code = directory / candidate
            if redirected_directory(code):
                raise ValueError("Community Store runtime directory is a link")
            validate_slot(code, version)
            runtime = start_runtime(code, root, version, notice, context, "")
        except Exception as error:
            if windows_lock_error(error):
                diagnostics.failure("loader.candidate_busy", error, version=diagnostics.version(version))
                save_state(directory, before_attempt)
                state = before_attempt
                notice = Message("bootstrap.update_state_failed")
                active = state.get("active")
                candidate = active if candidate == pending and active != candidate else None
                continue
            diagnostics.failure("loader.candidate_rejected", error, version=diagnostics.version(version))
            notice = reject(state, candidate)
            save_state(directory, state)
            candidate = state.get("active")
            continue
        if candidate == pending:
            state["previous"], state["active"], state["pending"] = state.get("active"), candidate, None
        state["trying"] = None
        try:
            save_state(directory, state)
        except Exception:
            # Stop this version before falling back to the bundled runtime.
            try:
                stop_runtime(runtime, context)
            except Exception as cleanup_error:
                diagnostics.failure("loader.cleanup_failed", cleanup_error)
            try:
                save_state(directory, before_attempt)
            except OSError as recovery_error:
                diagnostics.failure("loader.update_state_recovery_failed", recovery_error)
            raise
        return runtime, notice
    return None, notice


def launch(root, context):
    diagnostics.start()
    try:
        base_version = read_manifest(root)["version"]
        disabled = update_disabled(root)
    except Exception as error:
        diagnostics.failure("loader.initialization_failed", error)
        raise
    notice = ""
    if not disabled:
        try:
            directory = state_dir(root)
            if directory.exists():
                with update_lock(directory):
                    runtime, notice = select_runtime(directory, root, base_version, context)
                if runtime is not None:
                    return runtime
        except (OSError, ValueError) as error:
            diagnostics.failure("loader.update_state_failed", error)
            notice = Message("bootstrap.update_state_failed")
    return start_runtime(root, root.resolve(), base_version, notice, context, disabled)
