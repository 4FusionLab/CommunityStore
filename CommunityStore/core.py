"""Version comparison and GitHub source normalization."""
import re
from urllib.parse import urlsplit

from .localization import Message

ORG = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")
VERSION = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+)(?:\.(\d+))?)?(?:\+(?:[\w.-]+))?", re.I)


def version(value):
    match = VERSION.fullmatch(str(value).strip())
    return tuple(int(part or 0) for part in match.groups()) if match else None


def manifest_id(value):
    return value.strip().strip("{}").strip().casefold() if isinstance(value, str) else ""


def github_owner(value):
    candidate = value.strip().rstrip("/")
    if ORG.fullmatch(candidate):
        return candidate
    if candidate.lower().startswith(("github.com/", "www.github.com/")):
        candidate = "https://" + candidate
    url = urlsplit(candidate)
    parts = [part for part in url.path.split("/") if part]
    if url.scheme in ("http", "https") and url.netloc.lower() in ("github.com", "www.github.com") and parts:
        owner = parts[1] if parts[0] == "orgs" and len(parts) > 1 else parts[0]
        if ORG.fullmatch(owner):
            return owner
    raise ValueError(Message("core.owner_url"))
