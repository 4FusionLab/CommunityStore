"""Download and publish a verified Community Store runtime for the next startup."""
import re
import shutil
from urllib.error import HTTPError
from uuid import uuid4

from . import bootstrap, releases
from .localization import Message

REPO = "4FusionLab/CommunityStore"
SOURCE = {"repo": REPO, "name": "CommunityStore", "id": bootstrap.PRODUCT_ID}


def discover(root, current_version, cancelled):
    if bootstrap.update_disabled(root) or cancelled.is_set():
        return None
    directory = bootstrap.state_dir(root)
    state = bootstrap.read_state(directory)
    try:
        release = releases.github_json(f"repos/{REPO}/releases/latest")
    except HTTPError as error:
        if error.code == 404:
            return None
        raise
    tag = release.get("tag_name", "")
    version = tag.removeprefix("v")
    if (release.get("draft") or release.get("prerelease") or not bootstrap.VERSION.fullmatch(version)
            or bootstrap.version_key(version) <= bootstrap.version_key(current_version)):
        return None
    if not should_stage(state, version):
        return None
    name = f"CommunityStore-{version}.zip"
    url = f"https://github.com/{REPO}/releases/download/{tag}/{name}"
    assets = [asset for asset in release.get("assets", [])
              if asset.get("name") == name and asset.get("browser_download_url") == url]
    if len(assets) != 1 or not re.fullmatch(r"sha256:[0-9a-f]{64}", assets[0].get("digest") or ""):
        raise ValueError(Message("self_update.missing_zip"))
    return {"tag": tag, "version": version, "asset": assets[0]}


def stage(root, release, cancelled, *, progress=None):
    if bootstrap.update_disabled(root) or cancelled.is_set():
        return ""
    directory = bootstrap.state_dir(root)
    version = release["version"]
    if not bootstrap.VERSION.fullmatch(version) or release["tag"].removeprefix("v") != version:
        raise ValueError(Message("self_update.missing_zip"))
    if bootstrap.version_key(version) <= bootstrap.version_key(bootstrap.read_manifest(root)["version"]):
        return ""
    state = bootstrap.read_state(directory)
    if not should_stage(state, version):
        return pending_notice(state)
    work, package, _, _ = releases.stage(SOURCE, release, directory, progress=progress)
    try:
        bootstrap.validate_slot(package, version)
        with bootstrap.update_lock(directory):
            state = bootstrap.read_state(directory)
            if not cancelled.is_set() and should_stage(state, version):
                slot = f"{version}-{uuid4().hex}"
                destination = directory / slot
                package.rename(destination)
                state["pending"] = slot
                try:
                    bootstrap.save_state(directory, state)
                except Exception:
                    shutil.rmtree(destination, ignore_errors=True)
                    raise
            return pending_notice(state)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def should_stage(state, version):
    return (version not in state.get("rejected", [])
            and all(bootstrap.version_key(version) > bootstrap.version_key(bootstrap.slot_version(state[key]))
                    for key in ("active", "pending") if state.get(key)))


def pending_notice(state):
    pending = state.get("pending")
    if pending:
        return Message("self_update.pending", version=bootstrap.slot_version(pending))
    return ""
