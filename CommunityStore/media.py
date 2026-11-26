"""Bounded GitHub artwork and README cache for the palette."""
import base64
import hashlib
import json
import os
import re
import struct
import time
from pathlib import PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

IMAGE_LIMIT = 1024 * 1024
README_LIMIT = 96 * 1024
CACHE_LIMIT = 50 * 1024 * 1024
AVATAR_TTL = 86400
RELEASE_TTL = 7 * AVATAR_TTL
EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
ASSET_HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
RAW_HOST = "raw.githubusercontent.com"
AVATAR_HOST = "avatars.githubusercontent.com"
IMAGE_LINK = re.compile(
    r'''!\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^\s)\n]+))|<img\b[^>]*?\bsrc\s*=\s*["']([^"']+)["']''',
    re.IGNORECASE,
)


def allowed_url(url, hosts):
    try:
        parsed = urlsplit(url)
        return parsed.scheme == "https" and parsed.netloc in hosts
    except ValueError:
        return False


class AssetRedirect(HTTPRedirectHandler):
    def __init__(self, hosts):
        super().__init__()
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_url(newurl, self.hosts):
            raise URLError("Unexpected media redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def local_path(value):
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    path = unquote(parsed.path)
    parts = path.split("/")
    if parsed.scheme or parsed.netloc or "\\" in path or "\x00" in path or not path or any(part in ("", "..") for part in parts):
        return None
    result = PurePosixPath(path)
    return result.as_posix() if result.as_posix() != "." else None


def readme_images(markdown):
    previews, icons = [], []
    for match in IMAGE_LINK.finditer(markdown):
        path = local_path(next(value for value in match.groups() if value))
        if not path:
            continue
        file = PurePosixPath(path)
        if file.suffix.lower() not in EXTENSIONS or any(part in file.stem.casefold() for part in ("badge", "banner", "avatar", "shield")):
            continue
        group = icons if len(file.parts) == 1 or any(fragment in file.stem.casefold() for fragment in ("icon", "logo")) else previews
        if path not in group:
            group.append(path)
        if len(previews) + len(icons) >= 12:
            break
    return previews, icons


def image_type(payload):
    if len(payload) >= 24 and payload.startswith(b"\x89PNG\r\n\x1a\n") and payload[12:16] == b"IHDR":
        width, height = struct.unpack(">II", payload[16:24])
        return "png" if 0 < width <= 4096 and 0 < height <= 4096 else None
    if payload.startswith(b"\xff\xd8") and payload.endswith(b"\xff\xd9"):
        offset = 2
        while offset + 8 < len(payload) and payload[offset] == 255:
            marker = payload[offset + 1]
            length = int.from_bytes(payload[offset + 2:offset + 4], "big")
            if marker in (192, 193, 194):
                height, width = struct.unpack(">HH", payload[offset + 5:offset + 9])
                return "jpeg" if 0 < width <= 4096 and 0 < height <= 4096 else None
            if length < 2 or marker == 218:
                break
            offset += length + 2
    if len(payload) >= 30 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        kind = payload[12:16]
        if kind == b"VP8X":
            width = 1 + int.from_bytes(payload[24:27], "little")
            height = 1 + int.from_bytes(payload[27:30], "little")
        elif kind == b"VP8 " and payload[23:26] == b"\x9d\x01\x2a":
            width, height = (value & 0x3fff for value in struct.unpack("<HH", payload[26:30]))
        elif kind == b"VP8L" and payload[20] == 0x2f:
            size = int.from_bytes(payload[21:25], "little")
            width, height = 1 + (size & 0x3fff), 1 + ((size >> 14) & 0x3fff)
        else:
            return None
        return "webp" if 0 < width <= 4096 and 0 < height <= 4096 else None
    return None


def fetch(url, hosts, limit, raster, etag=None):
    if not allowed_url(url, hosts):
        return None
    accept = "image/png,image/jpeg,image/webp" if raster else "text/plain,application/octet-stream"
    headers = {"User-Agent": "Fusion-Community-Store", "Accept": accept}
    if etag:
        headers["If-None-Match"] = etag
    request = Request(url, headers=headers)
    with build_opener(AssetRedirect(hosts)).open(request, timeout=6) as response:
        content_type = response.headers.get_content_type()
        if raster and content_type not in {"image/png", "image/jpeg", "image/webp", "application/octet-stream"}:
            return None
        if not raster and content_type not in {"text/plain", "application/octet-stream", "text/markdown"}:
            return None
        payload = response.read(limit + 1)
        if len(payload) > limit or (raster and not image_type(payload)):
            return None
        return payload, response.headers.get("ETag")


class ArtworkCache:
    def __init__(self, folder):
        self.folder = folder
        self.retry_after = {}
        self.last_pruned = None

    def read(self, url, hosts, *, raster=False, revision="", ttl=RELEASE_TTL):
        digest = hashlib.sha256((url + "\0" + revision).encode()).hexdigest()
        target = self.folder / digest
        miss = target.with_suffix(".missing")
        validator = target.with_suffix(".etag")
        limit = IMAGE_LIMIT if raster else README_LIMIT
        previous, etag = None, None
        try:
            if target.is_file():
                with target.open("rb") as stream:
                    cached = stream.read(limit + 1)
                if len(cached) <= limit and (not raster or image_type(cached)):
                    previous = cached
                    if time.time() - target.stat().st_mtime < ttl:
                        return previous
                    if validator.is_file():
                        with validator.open(encoding="ascii") as stream:
                            etag = stream.read(512).strip()
                        if not re.fullmatch(r'(?:W/)?"[ -!#-~]{1,256}"', etag):
                            etag = None
            if miss.is_file() and time.time() - miss.stat().st_mtime < 3600:
                return previous
        except (OSError, UnicodeError):
            pass
        host = urlsplit(url).hostname
        payload = None
        if self.retry_after.get(host, 0) <= time.time():
            try:
                download = fetch(url, hosts, limit, raster, etag)
                if download is None:
                    self.save(miss, b"")
                else:
                    payload, new_etag = download
                    self.save(target, payload)
                    self.save(validator, (new_etag or "").encode("ascii", errors="ignore"))
            except HTTPError as error:
                try:
                    if error.code == 304 and previous is not None:
                        self.save(target, previous)
                    elif error.code in (403, 429):
                        now = time.time()
                        retry = error.headers.get("Retry-After", "")
                        reset = error.headers.get("X-RateLimit-Reset", "")
                        self.retry_after[host] = max(now + 60, now + int(retry) if retry.isdecimal() else 0,
                                                     int(reset) if reset.isdecimal() else 0)
                    elif error.code == 404:
                        self.save(miss, b"")
                finally:
                    error.close()
            except (OSError, ValueError):
                pass
        return payload if payload is not None else previous

    def save(self, path, payload):
        temporary = path.with_suffix("." + uuid4().hex + ".tmp")
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(payload)
            os.replace(temporary, path)
            now = time.monotonic()
            if self.last_pruned is None or now - self.last_pruned >= 60:
                self.last_pruned = now
                files = [(entry.stat(), entry) for entry in self.folder.iterdir()
                         if entry.is_file() and not entry.name.endswith(".tmp")]
                used = sum(stat.st_size for stat, _ in files)
                for stat, entry in sorted(files, key=lambda item: item[0].st_mtime):
                    if used <= CACHE_LIMIT:
                        break
                    entry.unlink(missing_ok=True)
                    used -= stat.st_size
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def image(self, url, hosts, *, revision="", ttl=RELEASE_TTL):
        data = self.read(url, hosts, raster=True, revision=revision, ttl=ttl)
        if data and (kind := image_type(data)):
            return f"data:image/{kind};base64," + base64.b64encode(data).decode("ascii")
        return None

    def avatar(self, url):
        if allowed_url(url, {AVATAR_HOST}):
            parsed = urlsplit(url)
            if re.fullmatch(r"/u/\d+", parsed.path):
                return self.image(f"https://{AVATAR_HOST}{parsed.path}?s=96&{parsed.query}", {AVATAR_HOST}, ttl=AVATAR_TTL)
        return None

    def readme(self, raw, language):
        names = ("README.ru.md", "README.md") if language == "ru" else ("README.md",)
        for name in names:
            data = self.read(raw + name, {RAW_HOST})
            if data:
                return name, data.decode("utf-8-sig", errors="replace")
        return "", ""

    def declared_artwork(self, raw):
        data = self.read(raw + "community-store.json", {RAW_HOST})
        if not data:
            return [], []
        try:
            config = json.loads(data)
            paths = config.get("screenshots", [])
            urls = [config.get("preview"), *(paths[:6] if isinstance(paths, list) else [])]
            icon = config.get("icon")
        except (ValueError, AttributeError):
            return [], []
        previews = [path for url in urls if isinstance(url, str) and (path := local_path(url)) and
                    PurePosixPath(path).suffix.casefold() in EXTENSIONS]
        icons = [path] if isinstance(icon, str) and (path := local_path(icon)) and PurePosixPath(path).suffix.casefold() in EXTENSIONS else []
        return list(dict.fromkeys(previews)), icons

    def artwork(self, source, language, details, *, publish_readme=None):
        repo, tag = source["repo"], source["tag"]
        raw = f"https://{RAW_HOST}/{repo}/{quote(tag, safe='')}/"
        filename, readme = self.readme(raw, language)
        documentation = {"about": readme, "readme": filename} if readme.strip() else {"readmeUnavailable": True}
        if details and publish_readme:
            publish_readme(documentation)
        previews, icons = readme_images(readme)
        declared_previews, declared_icons = self.declared_artwork(raw) if source.get("hasArtworkConfig") else ([], [])
        bundled = source.get("iconPaths")
        if bundled is None and source.get("addinName"):
            name = source["addinName"]
            bundled = [f"{name}/resources/32x32@2x.png", f"{name}/resources/32x32.png"]
        result = {}
        def images(paths):
            for path in paths:
                data = self.image(raw + quote(path), {RAW_HOST})
                if data:
                    yield data

        attachments = source.get("imageAssets", ())
        if icon := next(images(declared_icons), None):
            result["icon"] = icon
        for attachment in attachments:
            kind = attachment["kind"]
            if kind == "icon" and "icon" not in result:
                data = self.image(attachment["url"], ASSET_HOSTS, revision=attachment["revision"])
                if data:
                    result["icon"] = data
        if "icon" not in result:
            icon = next(images((bundled or [])[:2] + icons), None)
            if icon:
                result["icon"] = icon

        def gallery():
            yield from images(declared_previews)
            for attachment in attachments:
                if attachment["kind"] == "preview":
                    image = self.image(attachment["url"], ASSET_HOSTS, revision=attachment["revision"])
                    if image:
                        yield image
            yield from images(path for path in previews if path not in declared_previews)

        for index, image in enumerate(gallery()):
            result[f"shot{index}"] = image
            if index == (2 if details else 0):
                break
        if details and readme.strip():
            examples, illustrations = readme_images(readme)
            gallery_fields = {image: field for field, image in result.items()}
            paths = {}
            for path in (examples + illustrations)[:4]:
                image = next(images([path]), None)
                if image:
                    field = gallery_fields.get(image, f"readmeShot{len(paths)}")
                    result.setdefault(field, image)
                    paths[path] = field
            if paths:
                result["readmePaths"] = paths
        if details and not publish_readme:
            result.update(documentation)
        return result
