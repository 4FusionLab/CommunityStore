"""Read the public directory of default Marketplace sources."""
import json
from urllib.request import Request, urlopen

from .core import ORG


FALLBACK_OWNERS = ("4FusionLab",)
FILENAME = "trusted-stores.json"
URL = "https://raw.githubusercontent.com/4FusionLab/CommunityStore/refs/heads/main/" + FILENAME
MAX_BYTES = 16 * 1024
MAX_OWNERS = 32


def merge_owners(*groups):
    owners = {}
    for group in groups:
        for owner in group:
            if isinstance(owner, str) and ORG.fullmatch(owner):
                owners.setdefault(owner.casefold(), owner)
    return list(owners.values())


def parse(data):
    if (not isinstance(data, dict) or type(data.get("schemaVersion")) is not int
            or data["schemaVersion"] != 1):
        raise ValueError("Invalid trusted sources schema")
    owners = data.get("owners")
    if (not isinstance(owners, list) or len(owners) > MAX_OWNERS
            or any(not isinstance(owner, str) or not ORG.fullmatch(owner) for owner in owners)):
        raise ValueError("Invalid trusted GitHub owners")
    return tuple(merge_owners(owners))


def fetch():
    # This public file uses GitHub's raw endpoint without account credentials or REST requests.
    request = Request(URL, headers={"Accept": "application/json", "User-Agent": "Fusion-Community-Store"})
    with urlopen(request, timeout=5) as response:
        if response.geturl() != URL:
            raise ValueError("Unexpected trusted sources URL")
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("Trusted sources file too large")
    return parse(json.loads(body))
