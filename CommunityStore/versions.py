"""Resolve per-registration version targets and build explicit update plans."""
from . import releases
from .localization import Message

LATEST = "Latest"


def target_for(preferences, row):
    saved = preferences.get(row["key"], {})
    return saved.get("target", LATEST) if saved.get("id") == row["id"] else LATEST


def options(release):
    items = {item["tag"]: item for item in release.get("history", [])
             if releases.available(item)}
    if releases.available(release):
        items[release["tag"]] = release
    return sorted(items.values(), key=lambda item: releases.version(item["tag"]), reverse=True)


def select_release(release, target):
    if target == LATEST:
        return release if releases.available(release) else None
    return next((item for item in options(release) if item["tag"] == target), None)


def newer_stable_tag(installed_version, release):
    current = releases.version(installed_version)
    if current is None:
        return None
    candidates = []
    for item in (release, *release.get("history", ())):
        if item.get("draft") or item.get("prerelease"):
            continue
        tag = item.get("tag")
        parsed = releases.version(tag)
        if parsed is not None and parsed > current:
            candidates.append((parsed, tag))
    return max(candidates)[1] if candidates else None


def select_asset(release, name=None):
    assets = release["assets"]
    if not name and len(assets) == 1:
        name = assets[0]["name"]
    return next((asset for asset in assets if asset["name"] == name and asset["compatible"]), None)


def plan_row(row, source, release, target, *, busy=False, duplicate=None):
    result = {"key": row["key"], "name": row["name"], "version": row["version"],
              "target": target, "repo": source.get("repo"), "reason": "", "direction": "", "duplicate": duplicate or ""}
    selected = select_release(release, target)
    result["tag"] = selected["tag"] if selected else None
    current = releases.version(row["version"])
    remote = releases.version(result["tag"])
    from_folder = row.get("sourceType") == "folder"
    if row.get("self"):
        reason = Message("versions.self_update")
    elif row.get("checkout") and not from_folder:
        reason = Message("versions.git_checkout")
    elif duplicate:
        reason = duplicate
    elif not row["writable"] and not from_folder:
        reason = Message("versions.folder_locked")
    elif from_folder and not row.get("id") and (
            not source or row.get("github", "").casefold() != source["repo"].casefold()):
        reason = Message("versions.folder_identity_missing")
    elif busy:
        reason = Message("versions.busy")
    elif not source:
        reason = Message("versions.repository_missing")
    elif release.get("error"):
        reason = Message("versions.releases_unavailable")
    elif not selected:
        reason = (Message("versions.target_unavailable") if target != LATEST
                  else Message("versions.latest_unavailable"))
    elif current is None:
        reason = Message("versions.unknown_version")
    elif remote == current:
        reason = Message("versions.same_version")
    elif remote < current and target == LATEST:
        reason = Message("versions.newer_version")
    else:
        result["direction"] = "downgrade" if remote < current else "upgrade"
        if from_folder:
            result["fromFolder"] = True
        result["assets"] = [{k: asset[k] for k in ("name", "compatible")} for asset in selected["assets"]]
        reason = ""
    result["reason"] = reason
    return result
