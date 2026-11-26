"""Read the Fusion registration index and local add-in manifests."""
import configparser
import os
import re
import sys
from collections import Counter
from pathlib import Path

from .core import manifest_id
from .releases import PLATFORM, entry_point, manifest, matching_addin_name, supported_platforms
from .localization import Message


APP_STORE_LOCATION = 0
LINKED_LOCATION = 5
REMOVAL_ARCHIVE = ".community-store-removed"
ARCHIVED_NAME = re.compile(r".+-[0-9a-f]{8}", re.DOTALL)


def locations():
    home = Path.home()
    if sys.platform == "win32":
        roaming = Path(os.environ.get("APPDATA", home / "AppData/Roaming"))
        return [roaming / "Autodesk", Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Autodesk"]
    suffix = Path("Library/Application Support/Autodesk")
    return [
        home / suffix,
        home / "Library/Containers/com.autodesk.mas.fusion360/Data" / suffix,
        Path("/Library/Application Support/Autodesk"),
    ]


def install_root():
    return locations()[0] / "Autodesk Fusion 360/API/AddIns"


def addin_roots():
    for root in locations()[:2 if sys.platform == "darwin" else 1]:
        for product in ("Autodesk Fusion 360", "Autodesk Fusion"):
            yield root / product / "API/AddIns"


def github_origin(checkout):
    if not checkout:
        return ""
    config = configparser.RawConfigParser()
    try:
        git_dir = checkout / ".git"
        if git_dir.is_file():
            marker = git_dir.read_text(encoding="utf-8").strip()
            if not marker.startswith("gitdir: "):
                return ""
            git_dir = checkout / marker.removeprefix("gitdir: ")
            common = git_dir / "commondir"
            if common.is_file():
                git_dir = git_dir / common.read_text(encoding="utf-8").strip()
        config.read(git_dir / "config", encoding="utf-8")
        remote = config.get('remote "origin"', "url", fallback="")
    except (OSError, UnicodeError, configparser.Error):
        return ""
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+/[\w.-]+)", remote)
    return match.group(1).removesuffix(".git") if match else ""


def from_fusion(scripts, managed_root=None, *, include_hidden=False):
    rows = []
    objects = {}
    managed_root = managed_root.resolve() if managed_root else None
    for script in scripts:
        if not script.isAddIn or (not include_hidden and
                (not script.isVisible or script.isInternal or script.location not in (APP_STORE_LOCATION, 1, 2, LINKED_LOCATION))):
            continue
        folder = Path(os.path.abspath(script.folder))
        if not folder.is_dir():
            continue
        manifest_path = folder / (script.name + ".manifest")
        if not manifest_path.is_file():
            manifest_path = next(folder.glob("*.manifest"), manifest_path)
        info = manifest(manifest_path)
        entry = entry_point(manifest_path, PLATFORM) or folder / script.name
        resolved = folder.resolve()
        parents = (resolved, *resolved.parents)
        if managed_root and resolved.is_relative_to(managed_root):
            parents = tuple(parent for parent in parents if parent.is_relative_to(managed_root))
        checkout = next((parent for parent in parents if (parent / ".git").exists()), None)
        if managed_root in folder.parents and checkout and checkout in managed_root.parents:
            checkout = None
        location = int(script.location)
        linked = folder != resolved
        external = location == LINKED_LOCATION and resolved.parent != managed_root
        key = os.path.normcase(str(folder))
        rows.append({
            "key": key, "entry": str(entry), "folder": str(folder), "name": script.name,
            "id": manifest_id(info.get("id") or script.id), "version": str(info.get("version") or script.version or ""),
            "author": str(script.author or ""), "description": str(script.description or ""),
            "startup": bool(script.isRunOnStartup), "running": bool(script.isRunning),
            "registered": True, "location": location, "github": github_origin(checkout),
            "visible": bool(script.isVisible),
            "checkout": bool(checkout), "canonical": canonical_path(folder), "external": external,
            "writable": not (checkout or location == APP_STORE_LOCATION or linked or external)
                        and matching_addin_name(resolved.name, manifest_path.stem, PLATFORM)
                        and os.access(folder.parent, os.W_OK),
            "sourceType": ("app_store" if location == APP_STORE_LOCATION else "community_store"
                           if managed_root and resolved.parent == managed_root and not (checkout or linked or external)
                           else "folder"),
        })
        objects[key] = script
    registrations = Counter(row["canonical"] for row in rows)
    for row in rows:
        if registrations[row["canonical"]] > 1:
            row["sharedFolder"] = True
    return sorted(rows, key=lambda row: (row["name"].casefold(), row["key"])), objects


def archive_candidate(row, managed_root=None):
    if row.get("external") or row.get("sourceType") == "app_store" or row.get("location") == APP_STORE_LOCATION:
        return None
    folder = Path(row.get("folder") or Path(row["entry"]).parent)
    if row.get("canonical") and canonical_path(folder) != row["canonical"]:
        return None
    if folder.parent != managed_root and folder.parent not in addin_roots():
        return None
    if folder.is_symlink() or (row["writable"] and not row.get("sharedFolder")):
        return folder
    return None


def removal_archive_path(addin_root):
    archive = addin_root.parent / REMOVAL_ARCHIVE
    if not archive.is_absolute() or archive.resolve() != archive:
        raise OSError("Invalid removal archive path")
    return archive


def available_removal_archive(managed_root):
    candidates = []
    for root in dict.fromkeys((managed_root, *addin_roots())):
        try:
            archive = removal_archive_path(root)
            if any(ARCHIVED_NAME.fullmatch(item.name) and (item.is_symlink() or item.is_dir())
                   for item in archive.iterdir()):
                candidates.append((archive.stat().st_mtime_ns, archive))
        except (OSError, RuntimeError):
            continue
    return max(candidates, key=lambda candidate: candidate[0])[1] if candidates else None


def matches(source, row):
    if source.get("id") and row["id"]:
        if manifest_id(source["id"]) == row["id"]:
            return True
        if source.get("addinName", source["name"]).casefold() == row["name"].casefold():
            return False
    return (source.get("addinName", source["name"]).casefold() == row["name"].casefold()
            or source["repo"].casefold() == row.get("github", "").casefold())


def canonical_path(folder):
    return os.path.normcase(str(Path(folder).resolve()))


def conflict(rows, folder, identity, *, exclude=None):
    canonical = canonical_path(folder)
    identity = manifest_id(identity)
    return next((row for row in rows if row["key"] != exclude and (
        row["canonical"] == canonical or identity and row["id"] == identity
        and (row.get("visible", True) or row.get("sourceType") != "folder")
    )), None)


def duplicate_notice(row):
    return Message("inventory.possible_duplicate", name=row["name"], folder=row["folder"])


def local_addin(folder):
    candidates = [(path, info) for path in folder.glob("*.manifest") if (info := manifest(path))]
    if len(candidates) != 1:
        raise ValueError(Message("inventory.manifest_count"))
    path, info = candidates[0]
    identity = manifest_id(info.get("id"))
    if not identity or str(info.get("autodeskProduct", "Fusion360")).casefold() not in ("fusion", "fusion360"):
        raise ValueError(Message("inventory.manifest_id"))
    if PLATFORM not in supported_platforms(info):
        raise ValueError(Message("inventory.unsupported_os"))
    if not entry_point(path, PLATFORM):
        raise ValueError(Message("inventory.entry_missing"))
    return identity
