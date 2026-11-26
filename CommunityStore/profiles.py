"""Portable profile validation and destination-specific restore planning."""
import json

from . import releases, versions
from .bootstrap import PRODUCT_ID
from .core import ORG, manifest_id, version
from .localization import LANGUAGES, SYSTEM, Message
from .self_update import REPO as STORE_REPO

FORMAT = "fusion-community-store-profile"
CHECK_INTERVALS = (15, 30, 60, 120)
MAX_BYTES = 1024 * 1024


def require(condition):
    if not condition:
        raise ValueError(Message("profile.invalid"))


def short(value, limit=160):
    return isinstance(value, str) and 0 < len(value) <= limit and value.strip() and value.isprintable()


def fields(obj, allowed):
    require(isinstance(obj, dict) and obj.keys() <= set(allowed.split()))


def read(path):
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    require(len(data) <= MAX_BYTES)

    def unique(pairs):
        obj = dict(pairs)
        require(len(obj) == len(pairs))
        return obj

    try:
        return validate(json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique))
    except (ValueError, RecursionError):
        raise ValueError(Message("profile.invalid")) from None


def validate(doc):
    fields(doc, "format schemaVersion kind name settings ownerSubscriptions addins manual")
    require(doc.get("format") == FORMAT and type(doc.get("schemaVersion")) is int and doc["schemaVersion"] == 1)
    require(doc.get("kind") in ("transfer", "collection") and short(doc.get("name")))
    settings = doc.get("settings", {})
    fields(settings, "language automatic interval")
    if "language" in settings:
        require(settings["language"] in (SYSTEM, *LANGUAGES))
    if "automatic" in settings:
        require(type(settings["automatic"]) is bool)
    if "interval" in settings:
        require(type(settings["interval"]) is int and settings["interval"] in CHECK_INTERVALS)
    for key in ("ownerSubscriptions", "manual", "addins"):
        values = doc.get(key, [])
        require(isinstance(values, list) and len(values) <= 512)
    require(any(doc.get(k) for k in ("settings", "ownerSubscriptions", "manual", "addins")))
    for owner in doc.get("ownerSubscriptions", []):
        require(short(owner) and ORG.fullmatch(owner))
    for item in doc.get("manual", []):
        fields(item, "name reason")
        require(short(item.get("name")) and item.get("reason") == "noPublishedSource")
    repos, ids = set(), set()
    for item in doc.get("addins", []):
        fields(item, "repo manifestId installTarget updateTarget runOnStartup")
        repo = item.get("repo")
        require(short(repo))
        releases.validate_repo(repo)
        identity = manifest_id(item.get("manifestId"))
        require(short(identity) and identity != PRODUCT_ID and identity not in ids)
        require(repo.casefold() not in repos and repo.casefold() != STORE_REPO.casefold())
        for key in ("installTarget", "updateTarget"):
            target = item.get(key)
            require(short(target) and (target == versions.LATEST or version(target) is not None))
        if "runOnStartup" in item:
            require(doc["kind"] == "transfer" and type(item["runOnStartup"]) is bool)
        repos.add(repo.casefold())
        ids.add(identity)
    return doc


def plan_item(item, metadata, registered, preferences):
    repo = item["repo"]
    identity = manifest_id(item["manifestId"])
    release = versions.select_release(metadata, item["installTarget"])
    result = {"name": repo, "version": "", "tag": release["tag"] if release else item["installTarget"],
              "target": item["updateTarget"], "action": "install", "key": "", "assets": []}
    if "runOnStartup" in item:
        result["startup"] = item["runOnStartup"]

    def skip(reason):
        return {**result, "action": "skip", "reason": reason}

    remote_id = manifest_id(metadata.get("identity", {}).get("id"))
    if metadata.get("error") or not release:
        return skip(metadata.get("problem") or Message("runtime.compatible_release_unavailable"))
    if remote_id and remote_id != identity:
        return skip(Message("runtime.package_id_changed"))
    if not versions.select_release(metadata, item["updateTarget"]):
        return skip(Message("runtime.choose_target"))
    name = metadata.get("identity", {}).get("addinName") or repo.split("/")[1]
    matches = [row for row in registered if row["id"] == identity or row["name"].casefold() == name.casefold()]
    if matches:
        row = matches[0]
        result.update(version=row["version"], key=row["key"])
        saved = preferences.get("installedSources", {}).get(row["key"], {})
        if (len(matches) != 1 or row["id"] != identity or not row["visible"] or not row["writable"]
                or row["sourceType"] != "community_store" or saved.get("id") != identity
                or saved.get("repo", "").casefold() != repo.casefold()):
            return skip(Message("profile.conflict"))
        current, target = version(row["version"]), version(release["tag"])
        if current is None:
            return skip(Message("versions.unknown_version"))
        result["currentStartup"] = row["startup"]
        result["currentTarget"] = versions.target_for(preferences.get("targets", {}), row)
        if current == target and saved.get("tag") and saved["tag"] != release["tag"]:
            result["action"] = "replace"
        elif current == target:
            if (result["currentTarget"] == item["updateTarget"]
                    and row["startup"] == item.get("runOnStartup", row["startup"])):
                return skip(Message("profile.satisfied"))
            result["action"] = "configure"
        else:
            result["action"] = "upgrade" if current < target else "downgrade"
    if result["action"] != "configure":
        result["assets"] = [a["name"] for a in release["assets"] if a["compatible"]]
    return result
