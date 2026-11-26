"""Build browser destinations for Store reports from public catalog metadata."""
from urllib.parse import urlencode

from .bootstrap import VERSION
from .core import ORG
from .releases import REPO
from .self_update import REPO as STORE_REPO

GITHUB_REPO = f"https://github.com/{STORE_REPO}"


def security_policy_url(language):
    suffix = ".ru" if language == "ru" else ""
    return f"{GITHUB_REPO}/blob/main/SECURITY{suffix}.md"


def issue_url(kind, language, store_version, *, entry=None, owner=None):
    if kind not in ("addin", "source"):
        raise ValueError("Unknown report type")
    suffix = "-ru" if language == "ru" else ""
    fields = {"template": f"report-{kind}{suffix}.yml"}
    if isinstance(store_version, str) and len(store_version) <= 32 and VERSION.fullmatch(store_version):
        fields["store-version"] = store_version
    if kind == "source" and isinstance(owner, str) and ORG.fullmatch(owner):
        fields["github-owner"] = owner
    if kind == "addin" and entry:
        repo, tag = entry.get("repo"), entry.get("tag")
        if isinstance(repo, str) and REPO.fullmatch(repo) and len(repo) <= 140:
            fields["repository"] = repo
            # Keep even a percent-encoded Unicode tag within a short browser URL.
            if isinstance(tag, str) and tag.isprintable() and len(urlencode({"release-tag": tag})) <= 1400:
                fields["release-tag"] = tag
    return f"{GITHUB_REPO}/issues/new?{urlencode(fields)}"
