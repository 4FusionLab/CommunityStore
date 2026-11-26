"""Read public GitHub releases and stage verified Fusion ZIP packages."""
import base64
import hashlib
import json
import platform as host_platform
import re
import shutil
import stat
import sys
import tempfile
import time
import unicodedata
import zipfile
from contextlib import contextmanager
from contextvars import ContextVar
from http.client import HTTPException
from pathlib import Path, PurePosixPath
from threading import Event, Lock
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from . import diagnostics
from .core import ORG, manifest_id, version
from .media import EXTENSIONS, allowed_url
from .localization import Message

REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+")
RESERVED = re.compile(r"(?:con|prn|aux|nul|conin\$|conout\$|com[1-9¹²³]|lpt[1-9¹²³])", re.I)
MAX_DOWNLOAD = 150 * 1024**2
MAX_EXTRACT = 450 * 1024**2
PLATFORM = "windows" if sys.platform == "win32" else "mac"
MAC_ARCH = "arm64" if host_platform.machine().casefold() in {"arm64", "aarch64"} else "x64"
SCRIPT_SUFFIXES = (".py", ".ts", ".js")
NATIVE_SUFFIXES = {"windows": ".dll", "mac": ".dylib"}
ASSET_ARCHITECTURES = {"amd64": "x64", "x64": "x64", "intel": "x64", "arm64": "arm64", "aarch64": "arm64"}
HISTORY_TTL = 30 * 60
GITHUB_JSON = "application/vnd.github+json"
METADATA_PATH = re.compile(
    r"/users/[A-Za-z0-9-]+/repos"
    r"|/(?:repos/[A-Za-z0-9-]+/[A-Za-z0-9_.-]+|repositories/[0-9]+)"
    r"/(?:releases(?:/latest)?|git/(?:trees|blobs)/[^/]+)"
)
_api_lock = Lock()
_retry_at = 0
_session = ContextVar("github_catalog_session", default=None)


class RateLimited(Exception):
    """The shared GitHub REST API quota is temporarily unavailable."""


class SessionEnded(RateLimited):
    """The worker's authenticated session has ended."""


class GitHubSession:
    def __init__(self, token):
        self.token = token
        self.lock = Lock()
        self.ended = Event()
        self.retry_at = 0
        self.profile = None
        self.profile_loading = False


@contextmanager
def use_session(session):
    handle = _session.set(session)
    try:
        yield
    finally:
        _session.reset(handle)


def _metadata_url(url):
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.netloc == "api.github.com" and METADATA_PATH.fullmatch(parts.path)


def _account_url(url):
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.netloc == "api.github.com" and parts.path == "/user"


class MetadataRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _account_url(req.full_url) or not _metadata_url(newurl):
            return None
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected:
            redirected.add_unredirected_header("Authorization", req.get_header("Authorization"))
        return redirected


def _open_metadata(query):
    return build_opener(MetadataRedirect()).open(query, timeout=12)


def manifest(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict) and data.get("type", "").lower() == "addin":
            return data
    except (OSError, ValueError, AttributeError):
        pass
    return {}


def supported_platforms(info):
    return {name.strip() for name in str(info.get("supportedOS", "windows|mac")).casefold().split("|")}


def windows_dll(path):
    """Check the PE identity and architecture without loading the library."""
    try:
        with path.open("rb") as stream:
            dos = stream.read(64)
            if len(dos) != 64 or dos[:2] != b"MZ":
                return False
            offset = int.from_bytes(dos[60:64], "little")
            if offset < len(dos):
                return False
            stream.seek(offset)
            pe = stream.read(26)
    except OSError:
        return False
    return (len(pe) == 26 and pe[:4] == b"PE\0\0"
            and int.from_bytes(pe[4:6], "little") == 0x8664  # AMD64
            and int.from_bytes(pe[20:22], "little") >= 2  # Optional header size
            and int.from_bytes(pe[22:24], "little") & 0x2000  # IMAGE_FILE_DLL
            and int.from_bytes(pe[24:26], "little") == 0x20B)  # PE32+


def entry_point(path, target_platform):
    suffixes = (*SCRIPT_SUFFIXES, NATIVE_SUFFIXES[target_platform])
    for suffix in suffixes:
        entry = path.with_suffix(suffix)
        if entry.is_file() and (suffix != ".dll" or windows_dll(entry)):
            return entry
    return None


def matching_addin_name(folder_name, manifest_stem, target_platform):
    if target_platform == "windows":
        return folder_name.lower() == manifest_stem.lower()
    return folder_name == manifest_stem


def request(url, accept):
    query = Request(url, headers={
        "User-Agent": "Fusion-Community-Store",
        "Accept": accept,
    })
    if urlsplit(url).hostname != "api.github.com":
        return urlopen(query, timeout=12)
    account_request = _account_url(url)
    session = _session.get() if accept == GITHUB_JSON and (account_request or _metadata_url(url)) else None
    if account_request and not session:
        raise SessionEnded

    # Serialize REST requests through the response headers so a worker waiting
    # to send can see the limit discovered by the preceding worker.
    with session.lock if session else _api_lock:
        if session and session.ended.is_set():
            raise SessionEnded
        if retry_at(session) > time.time():
            raise RateLimited
        try:
            if session:
                query.add_unredirected_header("Authorization", "Bearer " + session.token)
                response = _open_metadata(query)
            else:
                response = urlopen(query, timeout=12)
        except HTTPError as error:
            if session and error.code == 401:
                session.ended.set()
                error.close()
                raise SessionEnded from None
            if not rate_limited(error):
                if session:
                    error.close()
                    raise HTTPError(url, error.code, "GitHub API request failed", {}, None) from None
                raise
            _record_rate_limit(error.headers or {}, session)
            error.close()
            raise RateLimited from None
        except (HTTPException, OSError):
            if session:
                raise OSError(Message("github.request_failed")) from None
            raise
        if response.headers.get("X-RateLimit-Remaining") == "0":
            _record_rate_limit(response.headers, session)
        return response


def retry_at(session=None):
    return session.retry_at if session else _retry_at


def restore_rate_limit(deadline):
    if type(deadline) is int and deadline > time.time():
        with _api_lock:
            _record_deadline(deadline)


def _record_deadline(deadline, session=None):
    global _retry_at
    if session:
        session.retry_at = max(session.retry_at, deadline)
    else:
        _retry_at = max(_retry_at, deadline)


def _record_rate_limit(headers, session):
    now = int(time.time()) + 1
    reset = _header_number(headers, "X-RateLimit-Reset")
    delay = _header_number(headers, "Retry-After")
    remaining = headers.get("X-RateLimit-Remaining")
    deadline = max(reset if remaining in (None, "0") else 0, now + delay if delay else 0)
    _record_deadline(deadline if deadline >= now else now + 60, session)


def _header_number(headers, name):
    value = headers.get(name, "")
    return int(value) if re.fullmatch(r"[0-9]{1,12}", value) else 0


def rate_limited(error):
    if error.code != 403:
        return error.code == 429
    headers = error.headers or {}
    return (headers.get("X-RateLimit-Remaining") == "0" or bool(headers.get("Retry-After"))
            or "rate limit" in str(error.reason).lower()
            or bool(error.fp and b"rate limit" in error.read(4096).lower()))


def public_repositories(owner, *, page=1, advance=None):
    """Yield repositories, reporting the next page after safely reading each page."""
    if not ORG.fullmatch(owner):
        raise ValueError(Message("releases.organization"))
    while True:
        url = f"https://api.github.com/users/{owner}/repos?type=owner&per_page=100&page={page}"
        with request(url, GITHUB_JSON) as response:
            repositories = json.loads(response.read(4 * 1024**2))
            next_page = 'rel="next"' in response.headers.get("Link", "")
        for data in repositories:
            if not (data.get("private") or data.get("fork") or data.get("archived") or data["name"].startswith(".")):
                yield {"repo": data["full_name"], "name": data["name"], "description": data.get("description") or "",
                       "topics": data.get("topics", []), "stars": data.get("stargazers_count", 0),
                       "ownerAvatar": data.get("owner", {}).get("avatar_url", "")}
        if not next_page:
            return
        page += 1
        if advance:
            advance(page)


def release_description(body):
    for line in body.splitlines():
        line = line.strip().lstrip("-* ").strip()
        if line and not line.startswith(("#", "<", "![", "[", "|", "```")):
            return line[:1600]
    return ""


ADDIN_TOPICS = {"fusion360-addin", "fusion-addin", "autodesk-fusion-addin", "fusion-360-addin"}


def github_json(path):
    with request(f"https://api.github.com/{path}", GITHUB_JSON) as response:
        return json.loads(response.read(4 * 1024**2))


def authenticated_profile(session):
    with use_session(session):
        data = github_json("user")
    login = data.get("login") if isinstance(data, dict) else None
    if not isinstance(login, str) or not ORG.fullmatch(login):
        raise ValueError("Invalid GitHub account login")
    name, avatar = data.get("name"), data.get("avatar_url")
    return {"login": login, "name": name.strip()[:160] if isinstance(name, str) else "",
            "avatar": avatar if isinstance(avatar, str) and len(avatar) <= 2048
            and allowed_url(avatar, {"avatars.githubusercontent.com"}) else ""}


def manifest_identity(info, name):
    author = info.get("author")
    return {"id": str(info.get("id", "")), "addinName": name,
            "author": author.strip()[:160] if isinstance(author, str) else ""}


def release_identity(source, tag):
    repo = source["repo"]
    try:
        tree = github_json(f"repos/{repo}/git/trees/{quote(tag, safe='')}?recursive=1")
    except HTTPError as error:
        if error.code in (404, 409, 422):
            return {}
        raise
    files = {item["path"]: item["sha"] for item in tree.get("tree", []) if item["type"] == "blob"}
    expected = source["name"].casefold()
    candidates = []
    for filename in files:
        path = PurePosixPath(filename)
        if path.suffix != ".manifest" or any(
            part.lower() in {"tests", "test", "samples", "examples", "docs"} for part in path.parts[:-1]
        ):
            continue
        if any(path.with_suffix(ext).as_posix() in files for ext in (*SCRIPT_SUFFIXES, *NATIVE_SUFFIXES.values(), ".cpp")):
            candidates.append(path)
    candidates.sort(key=lambda path: (path.stem.casefold() != expected, len(path.parts), path.as_posix()))
    for path in candidates[:6]:
        blob = github_json(f"repos/{repo}/git/blobs/{files[path.as_posix()]}")
        if blob.get("encoding") != "base64" or blob.get("size", 0) > 32768:
            continue
        try:
            info = json.loads(base64.b64decode(blob["content"]).decode("utf-8-sig"))
            if (str(info.get("type", "")).casefold() == "addin" and
                    str(info.get("autodeskProduct", "Fusion360")).casefold() in ("fusion", "fusion360")):
                return {**manifest_identity(info, path.stem),
                        "iconPaths": bundled_icons(files, path.parent),
                        "hasArtworkConfig": "community-store.json" in files}
        except (ValueError, AttributeError, KeyError):
            pass
    return {}


def bundled_icons(files, package):
    candidates = []
    for filename in files:
        path = PurePosixPath(filename)
        if not path.is_relative_to(package / "resources"):
            continue
        if path.name.lower() in ("32x32@2x.png", "64x64.png", "32x32.png"):
            candidates.append(path)
    candidates.sort(key=lambda path: (
        "application" not in path.parts, len(path.parts),
        path.name.lower() == "32x32.png", path.as_posix(),
    ))
    return [path.as_posix() for path in candidates[:3]]


def artwork_assets(assets, repo):
    pictures = []
    for asset in assets:
        name = asset.get("name", "")
        url = asset.get("browser_download_url", "")
        if (PurePosixPath(name).suffix.lower() not in EXTENSIONS or
                not allowed_url(url, {"github.com"}) or
                not urlsplit(url).path.casefold().startswith(f"/{repo.casefold()}/releases/download/")):
            continue
        stem = PurePosixPath(name).stem.casefold()
        if any(text in stem for text in ("badge", "banner", "avatar", "shield")):
            continue
        pictures.append({
            "kind": "icon" if "icon" in stem or "logo" in stem else "preview",
            "url": url, "revision": str(asset.get("digest") or asset.get("updated_at") or asset.get("id") or ""),
        })
    return pictures[:8]


def validate_repo(repo):
    if not isinstance(repo, str) or not REPO.fullmatch(repo) or repo.split("/")[1] in (".", ".."):
        raise ValueError(Message("releases.repository"))


def release_option(data, repo):
    tag = data.get("tag_name", "")
    return {
        "tag": tag,
        "prerelease": bool(data.get("prerelease")),
        "draft": bool(data.get("draft")),
        "notes": (data.get("body") or "")[:16000],
        "publishedAt": data.get("published_at") or "",
        "imageAssets": artwork_assets(data.get("assets", []), repo),
        "assets": ranked_assets(data.get("assets", []), repo, tag),
    }


def ranked_assets(assets, repo, tag):
    candidates = [(package_rank(item, repo), item) for item in assets if release_zip(item, repo, tag)]
    candidates.sort(key=lambda candidate: (-candidate[0], candidate[1]["name"].casefold()))
    return [{
        **{key: item.get(key) for key in ("name", "browser_download_url", "digest")},
        "compatible": bool(rank and isinstance(item.get("digest"), str)
                           and re.fullmatch(r"sha256:[a-f0-9]{64}", item["digest"])),
    } for rank, item in candidates]


def refresh_cached_release(release, repo):
    def refresh(option):
        if "assets" not in option:
            return option
        assets = option["assets"]
        return {**option, "assets": ranked_assets(assets if isinstance(assets, list) else [], repo, option.get("tag", ""))}

    restored = refresh(release)
    if "history" not in release:
        return restored
    history = release["history"]
    if not isinstance(history, list):
        return {key: value for key, value in restored.items() if key != "history"}
    return {**restored, "history": [refresh(item) for item in history if isinstance(item, dict)]}


def available(release, *, stable_only=True):
    return (version(release.get("tag")) is not None and not release.get("draft")
            and (not stable_only or not release.get("prerelease")) and any(asset["compatible"] for asset in release.get("assets", [])))


def history(repo):
    validate_repo(repo)
    options = []
    page = 1
    while True:
        url = f"https://api.github.com/repos/{repo}/releases?per_page=100&page={page}"
        try:
            with request(url, GITHUB_JSON) as response:
                data = json.loads(response.read(32 * 1024**2))
                next_page = 'rel="next"' in response.headers.get("Link", "")
        except HTTPError as error:
            if page != 1 or error.code != 404:
                raise
            return []
        options.extend(release_option(item, repo) for item in data if not item.get("draft"))
        if not next_page:
            return options
        page += 1


def release_history(repo, latest_tag):
    return {"history": history(repo), "historyLatestTag": latest_tag, "historyChecked": int(time.time())}


def latest(source, previous=None, *, with_history=False, refresh_history=True, inspect_package=True):
    repo = source["repo"]
    validate_repo(repo)
    try:
        data = github_json(f"repos/{repo}/releases/latest")
    except HTTPError as error:
        if error.code != 404:
            raise
        data = {}
    option = release_option(data, repo)
    previous = previous or {}
    restored = refresh_cached_release(previous, repo) if "history" in previous else {}
    history_cache = {key: restored[key] for key in ("history", "historyLatestTag", "historyChecked")
                     if key in restored}
    checked = history_cache.get("historyChecked")
    fresh = ("history" in history_cache and history_cache.get("historyLatestTag") == option["tag"]
             and type(checked) is int and time.time() - checked < HISTORY_TTL)
    compatible = available(option)
    if with_history or (not fresh and (not compatible or (refresh_history and "history" in history_cache))):
        try:
            history_cache = release_history(repo, option["tag"])
        except (OSError, ValueError, HTTPException, RateLimited) as error:
            if with_history or not compatible:
                raise
            diagnostics.failure("catalog.release_history_failed", error)
    all_options = history_cache.get("history")
    selected = option if compatible else next(
        (item for item in all_options if available(item)), None)
    if not selected:
        selected = option if option["tag"] else next(iter(all_options), None)
    if not selected:
        return {"error": "no_releases", **history_cache}
    tag, assets = selected["tag"], selected["assets"]
    description = source.get("description", "")
    declared_addin = bool(ADDIN_TOPICS.intersection(source.get("topics", [])))
    cached_identity = previous.get("identity", {})
    if (previous.get("tag") == tag and previous.get("identityChecked")
            and (not cached_identity or "author" in cached_identity)):
        identity = cached_identity
    else:
        identity = release_identity(source, tag)
        if not identity and not declared_addin and inspect_package:
            identity = packaged_identity(source, tag, assets)
    return {
        **selected, "identity": identity, "identityChecked": True, "isAddin": bool(identity or declared_addin),
        **history_cache,
        "description": description or release_description(selected["notes"]),
        "stars": int(source.get("stars", 0)),
    }


def release_zip(asset, repo, tag=None):
    if not isinstance(asset, dict):
        return False
    url_text, name = asset.get("browser_download_url"), asset.get("name")
    if not isinstance(url_text, str) or not isinstance(name, str):
        return False
    if re.search(r"[\x00-\x1f\x7f/\\]", name) or re.search(r"[\x00-\x20\x7f]", url_text):
        return False
    try:
        url = urlsplit(url_text)
    except ValueError:
        return False
    prefix = f"/{repo}/releases/download/"
    if (url.scheme != "https" or url.netloc != "github.com" or url.query or url.fragment
            or not url.path.casefold().startswith(prefix.casefold()) or not name.lower().endswith(".zip")):
        return False
    asset_tag, _, filename = url.path[len(prefix):].rpartition("/")
    return bool(asset_tag and unquote(filename) == name and (tag is None or unquote(asset_tag) == tag))


def package_rank(asset, repo, platform=None):
    platform = platform or PLATFORM
    if not release_zip(asset, repo):
        return 0
    stem = asset["name"][:-4].casefold()
    product = repo.rsplit("/", 1)[-1].casefold()
    if re.match(re.escape(product) + r"(?:[-_.]|$)", stem):
        stem = stem[len(product):]
    stem = re.sub(r"(^|[-_.])x86[-_.]64(?=[-_.]|$)", r"\1amd64", stem)
    stem = re.sub(r"(^|[-_.])(?:win|windows)[-_.]32(?=[-_.]|$)", r"\1win32", stem)
    tokens = set(re.split(r"[-_.]+", stem))
    if tokens & {"source", "src", "symbols", "debug", "linux", "win32", "windows32", "x86", "i386", "i686", "arm", "arm32",
                 "winarm64", "windowsarm64"}:
        return 0
    mac = bool(tokens & {"mac", "macos", "darwin", "osx"})
    windows = bool(tokens & {"win", "windows", "win64", "windows64"})
    if ((mac or "universal2" in tokens) and platform != "mac") or (windows and platform != "windows"):
        return 0
    architectures = {ASSET_ARCHITECTURES[token] for token in tokens & ASSET_ARCHITECTURES.keys()}
    expected = "x64" if platform == "windows" else MAC_ARCH
    if architectures and (not (mac or windows) or architectures != {expected}):
        return 0
    return 3 if mac or windows else 2 if tokens & {"universal", "universal2"} else 1


def extract(zip_path, target):
    with zipfile.ZipFile(zip_path) as archive:
        items = archive.infolist()
        if len(items) > 15000 or sum(item.file_size for item in items) > MAX_EXTRACT:
            raise ValueError(Message("releases.unpacked_size"))
        paths = {}
        explicit = set()
        for item in items:
            raw = item.orig_filename.removesuffix("/")
            path = PurePosixPath(raw)
            if (not raw or path.is_absolute() or re.search(r'[\x00-\x1f<>:"\\|?*]', raw)
                    or any(part in ("", ".", "..") or part.rstrip(" .") != part for part in raw.split("/"))
                    or stat.S_ISLNK(item.external_attr >> 16)
                    or any(RESERVED.fullmatch(part.partition(".")[0].rstrip(" ")) for part in path.parts)):
                raise ValueError(Message("releases.unsafe_path"))
            # Implicit directories must also agree on spelling and kind.
            for member in (*reversed(path.parents[:-1]), path):
                key = unicodedata.normalize("NFC", member.as_posix().casefold())
                descriptor = (member, member != path or item.is_dir())
                if paths.setdefault(key, descriptor) != descriptor:
                    raise ValueError(Message("releases.duplicate_path"))
            if key in explicit:
                raise ValueError(Message("releases.duplicate_path"))
            explicit.add(key)
            destination = target.joinpath(*path.parts)
            if item.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output)


def packaged_identity(source, tag, assets):
    if not version(tag):
        return {}
    with tempfile.TemporaryDirectory(prefix="community-store-discovery-") as directory:
        for asset in assets:
            if not any(package_rank(asset, source["repo"], platform) for platform in ("mac", "windows")):
                continue
            if not asset.get("digest"):
                continue
            try:
                _, package, _, info = stage(source, {"tag": tag, "asset": asset},
                                            Path(directory), check_platform=False)
            except (ValueError, zipfile.BadZipFile):
                continue
            return manifest_identity(info, package.name)
    return {}


def stage(source, release, work_root, *, check_platform=True, progress=None, allow_missing_id=False):
    asset = release["asset"]
    if not release_zip(asset, source["repo"], release["tag"]):
        raise ValueError(Message("releases.download_url"))
    if check_platform and not package_rank(asset, source["repo"]):
        raise ValueError(Message("releases.zip_os"))
    digest = asset.get("digest", "")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest or ""):
        raise ValueError(Message("releases.missing_digest"))
    work_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="package-", dir=work_root))
    try:
        archive = work / "release.zip"
        checksum = hashlib.sha256()
        size = 0
        with request(asset["browser_download_url"], "application/octet-stream") as response, archive.open("wb") as output:
            final = urlsplit(response.geturl())
            if final.scheme != "https" or final.netloc not in (
                "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com",
            ):
                raise ValueError(Message("releases.download_url"))
            length = getattr(response, "headers", {}).get("Content-Length", "") if progress else ""
            total = int(length) if re.fullmatch(r"[0-9]{1,10}", length) and 0 < int(length) <= MAX_DOWNLOAD else 0
            reported_at = 0
            if progress:
                progress("downloading", 0, total)
            while chunk := response.read(1024**2):
                size += len(chunk)
                if size > MAX_DOWNLOAD:
                    raise ValueError(Message("releases.zip_size"))
                checksum.update(chunk)
                output.write(chunk)
                if progress and time.monotonic() - reported_at >= 0.2:
                    reported_at = time.monotonic()
                    progress("downloading", size, total if size <= total else 0)
        if progress:
            progress("verifying", size, total)
        if "sha256:" + checksum.hexdigest() != digest:
            raise ValueError(Message("releases.digest_mismatch"))
        expanded = work / "expanded"
        if progress:
            progress("extracting", size, total)
        extract(archive, expanded)
        candidates = []
        for path in expanded.rglob("*.manifest"):
            info = manifest(path)
            if not info:
                continue
            if source.get("id"):
                matches = manifest_id(info.get("id")) == manifest_id(source["id"])
            else:
                matches = path.stem.casefold() == source["name"].casefold()
            if matches:
                candidates.append((path, info))
        if not candidates and not source.get("id") and not allow_missing_id:
            candidates = [(path, info) for path in expanded.rglob("*.manifest") if (info := manifest(path))]
        if len(candidates) != 1:
            raise ValueError(Message("releases.zip_manifest"))
        path, info = candidates[0]
        package_id = info.get("id", "")
        product = str(info.get("autodeskProduct", "Fusion360")).casefold()
        if product not in ("fusion", "fusion360") or (
                not manifest_id(package_id) and (not allow_missing_id or package_id != "")):
            raise ValueError(Message("releases.zip_manifest_id"))
        if not re.fullmatch(r"[A-Za-z0-9_. -]+", path.stem) or path.stem in (".", ".."):
            raise ValueError(Message("releases.zip_addin_name"))
        if not version(info.get("version")) or version(info["version"]) != version(release["tag"]):
            raise ValueError(Message("releases.zip_version"))
        platforms = (PLATFORM,) if check_platform else tuple(NATIVE_SUFFIXES)
        supported_os = supported_platforms(info)
        supported = [name for name in platforms if name in supported_os]
        if not supported:
            raise ValueError(Message("releases.zip_os"))
        matching = [name for name in supported if matching_addin_name(path.parent.name, path.stem, name)]
        if not matching:
            raise ValueError(Message("releases.zip_folder"))
        entry = next((candidate for name in matching if (candidate := entry_point(path, name))), None)
        if entry is None:
            raise ValueError(Message("inventory.entry_missing"))
        return work, path.parent, entry.name, info
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
