import base64
import hashlib
import io
import json
import stat
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from CommunityStore import inventory, releases, versions


def pe_image(*, machine=0x8664, flags=0x2002, magic=0x20B):
    dos = bytearray(64)
    dos[:2] = b"MZ"
    struct.pack_into("<I", dos, 60, len(dos))
    optional = struct.pack("<H", magic).ljust(112, b"\0")
    return dos + struct.pack("<4sHHIIIHH", b"PE\0\0", machine, 1, 0, 0, 0, len(optional), flags) + optional


class RateLimitTests(unittest.TestCase):
    url = "https://api.github.com/repos/Owner/Example/releases/latest"
    accept = "application/vnd.github+json"

    def test_primary_limit_stops_other_requests_until_reset(self):
        headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1100"}
        error = HTTPError(self.url, 403, "Forbidden", headers, None)
        response = io.BytesIO(b"{}")
        response.headers = {}
        with (patch.object(releases, "_retry_at", 0),
              patch.object(releases.time, "time", return_value=1000) as clock,
              patch.object(releases, "urlopen", side_effect=[error, response]) as send):
            with self.assertRaises(releases.RateLimited):
                releases.request(self.url, self.accept)
            self.assertEqual(releases.retry_at(), 1100)
            clock.return_value = 1099
            with self.assertRaises(releases.RateLimited):
                releases.request("https://api.github.com/users/Owner/repos", self.accept)
            send.assert_called_once()
            clock.return_value = 1100
            self.assertIs(releases.request(self.url, self.accept), response)
            self.assertEqual(send.call_count, 2)

    def test_secondary_limit_uses_retry_after_or_a_one_minute_wait(self):
        for headers, message, expected in (
            ({"Retry-After": "90", "X-RateLimit-Remaining": "10", "X-RateLimit-Reset": "2000"}, "Forbidden", 1091),
            ({}, "API rate limit exceeded", 1061),
        ):
            error = HTTPError(self.url, 403, "Forbidden", headers, io.BytesIO(message.encode()))
            with (self.subTest(headers=headers), patch.object(releases, "_retry_at", 0),
                  patch.object(releases.time, "time", return_value=1000),
                  patch.object(releases, "urlopen", side_effect=error)):
                with self.assertRaises(releases.RateLimited):
                    releases.request(self.url, self.accept)
                self.assertEqual(releases.retry_at(), expected)

    def test_last_successful_request_can_start_pause_and_restored_deadline_blocks_network(self):
        response = io.BytesIO(b"{}")
        response.headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1100"}
        with (patch.object(releases, "_retry_at", 0), patch.object(releases.time, "time", return_value=1000),
              patch.object(releases, "urlopen", return_value=response)):
            self.assertIs(releases.request(self.url, self.accept), response)
            deadline = releases.retry_at()
        with (patch.object(releases, "_retry_at", 0), patch.object(releases.time, "time", return_value=1001),
              patch.object(releases, "urlopen") as send):
            releases.restore_rate_limit(deadline)
            with self.assertRaises(releases.RateLimited):
                releases.request(self.url, self.accept)
            send.assert_not_called()

    def test_ordinary_forbidden_response_does_not_start_pause(self):
        error = HTTPError(self.url, 403, "Forbidden", {}, io.BytesIO(b"Access denied"))
        with error, patch.object(releases, "_retry_at", 0), patch.object(releases, "urlopen", side_effect=error):
            with self.assertRaises(HTTPError):
                releases.request(self.url, self.accept)
            self.assertEqual(releases.retry_at(), 0)


class ReleasePackageTests(unittest.TestCase):
    def setUp(self):
        self.source = {"repo": "Owner/Example", "name": "Example", "id": "manifest-id"}
        self.info = {"type": "addin", "id": "manifest-id", "version": "1.2.0", "supportedOS": "mac|windows"}

    def package(self, info, bad_member=None, *, entries=None, folder="Example"):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(f"{folder}/Example.manifest", json.dumps(info))
            if entries is None:
                entries = {"Example.py": "def run(context): pass"}
            for filename, content in entries.items():
                archive.writestr(f"{folder}/{filename}", content)
            if bad_member:
                archive.writestr(bad_member, "payload")
        return buffer.getvalue()

    def stage(self, root, data, digest=None, *, progress=None, filename="Example.zip", **options):
        url = f"https://github.com/Owner/Example/releases/download/v1.2.0/{filename}"
        release = {"tag": "v1.2.0", "asset": {
            "name": filename,
            "browser_download_url": url,
            "digest": digest or "sha256:" + hashlib.sha256(data).hexdigest(),
        }}
        response = io.BytesIO(data)
        response.geturl = lambda: url
        response.headers = {"Content-Length": str(len(data))}
        with patch.object(releases, "request", return_value=response):
            return releases.stage(self.source, release, root, progress=progress, **options)

    def test_valid_release_stages_without_executing(self):
        with tempfile.TemporaryDirectory() as directory:
            data = self.package(self.info)
            events = []
            work, package, entry, info = self.stage(Path(directory), data, progress=lambda *event: events.append(event))
            self.assertTrue((package / entry).is_file())
            self.assertEqual(info, self.info)
            self.assertEqual(package.relative_to(work).as_posix(), "expanded/Example")
            self.assertEqual(events[0], ("downloading", 0, len(data)))
            self.assertEqual(events[-2:], [("verifying", len(data), len(data)), ("extracting", len(data), len(data))])

    def test_manifest_id_comparison_handles_braces_and_case(self):
        with tempfile.TemporaryDirectory() as directory:
            info = {**self.info, "id": "{MANIFEST-ID}"}
            _, _, _, manifest = self.stage(Path(directory), self.package(info))
            self.assertEqual(manifest["id"], "{MANIFEST-ID}")

    def test_idless_update_requires_exact_manifest_name_and_matching_repository(self):
        self.info.pop("id")
        self.source.pop("id")
        data = self.package(self.info)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                self.stage(root, data)
            work, package, entry, info = self.stage(root, data, allow_missing_id=True)
            self.assertEqual((package.name, entry, info), ("Example", "Example.py", self.info))
            self.source["id"] = "manifest-id"
            with self.assertRaises(ValueError):
                self.stage(root, data, allow_missing_id=True)
            self.source.pop("id")
            self.source["name"] = "Different"
            with self.assertRaises(ValueError):
                self.stage(root, data, allow_missing_id=True)
            self.source.update(name="Example", repo="Other/Repo")
            with self.assertRaises(ValueError):
                self.stage(root, data, allow_missing_id=True)
            self.assertEqual(list(root.iterdir()), [work])

    def test_rejects_mismatched_digest_id_and_version_and_cleans_staging(self):
        cases = [
            (self.info, "sha256:" + "0" * 64),
            ({**self.info, "id": "other-id"}, None),
            ({**self.info, "version": "1.1.0"}, None),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for info, digest in cases:
                with self.subTest(info=info, digest=digest), self.assertRaises(ValueError):
                    self.stage(root, self.package(info), digest)
                self.assertEqual(list(root.iterdir()), [])

    def test_rejects_path_traversal_and_zip_symlink(self):
        symlink = zipfile.ZipInfo("Example/link")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        with tempfile.TemporaryDirectory() as directory:
            for item in ("../escape.py", "Example/../escape.py",
                         "Example/bad?.py", "Example/files//", "Example/NUL.txt", "Example/CONIN$.txt",
                         "Example/conout$.data/file", "Example/AUX .txt", "Example/LPT².log",
                         "Example/folder./file", "Example/file ", symlink):
                with self.subTest(item=item), self.assertRaises(ValueError):
                    self.stage(Path(directory), self.package(self.info, item))

    def test_raw_member_name_is_checked_before_using_the_normalized_name(self):
        # ZipInfo retains the decoded header in orig_filename. A Windows ZIP
        # writer normalizes separators before writing, so model the reader here.
        member = zipfile.ZipInfo("Example/file.txt")
        member.orig_filename = r"Example\file.txt"
        member.file_size = 0
        with tempfile.TemporaryDirectory() as directory, patch.object(zipfile, "ZipFile") as reader:
            archive = reader.return_value.__enter__.return_value
            archive.infolist.return_value = [member]
            with self.assertRaises(ValueError) as caught:
                releases.extract(None, Path(directory))
            self.assertEqual(caught.exception.args[0].key, "releases.unsafe_path")
            archive.open.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_rejects_ambiguous_directory_paths_on_case_sensitive_hosts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for item in ("example/other.txt", "Example", "Example/Example.py/other.txt"):
                with self.subTest(item=item), self.assertRaises(ValueError):
                    self.stage(root, self.package(self.info, item))
                self.assertEqual(list(root.iterdir()), [])

    def test_source_repository_tag_and_asset_are_validated_before_request(self):
        url = "https://github.com/Owner/Example/releases/download/v1.2.0/Example.zip"
        urls = (url.replace("Owner/Example", "Different/Example"), url.replace("v1.2.0", "v1.1.0"),
                url.replace("Example.zip", "Different.zip"), url + "?unexpected=value")
        with tempfile.TemporaryDirectory() as directory, patch.object(releases, "request") as request:
            for invalid_url in urls:
                asset = {"name": "Example.zip", "browser_download_url": invalid_url,
                         "digest": "sha256:" + "a" * 64}
                with self.subTest(url=invalid_url), self.assertRaises(ValueError):
                    releases.stage(self.source, {"tag": "v1.2.0", "asset": asset}, Path(directory))
            request.assert_not_called()

    def test_fusion_product_is_required_even_for_a_matching_addin_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                self.stage(root, self.package({**self.info, "autodeskProduct": "Inventor"}))
            self.assertEqual(list(root.iterdir()), [])
            self.stage(root, self.package({**self.info, "supportedOS": "Windows | Mac"}))

    def test_ranks_platform_asset_above_universal_and_generic(self):
        def rank(filename):
            return releases.package_rank({
                "name": filename,
                "browser_download_url": "https://github.com/Owner/Example/releases/download/v1.2.0/" + filename,
            }, self.source["repo"])

        with patch.object(releases, "PLATFORM", "mac"):
            self.assertGreater(rank("Example-macos.zip"), rank("Example-universal.zip"))
            self.assertGreater(rank("Example-universal.zip"), rank("Example.zip"))
            self.assertEqual(rank("Example-source.zip"), 0)
            self.assertEqual(rank("Example-windows.zip"), 0)

    def test_platform_and_architecture_tokens_do_not_match_product_names_or_substrings(self):
        with patch.object(releases, "MAC_ARCH", "arm64"):
            for filename, expected in (
                ("Example-windows-x64.zip", (3, 0)), ("Example_win64_x86_64.zip", (3, 0)),
                ("Example-windows-amd64.zip", (3, 0)), ("Example-win-arm64.zip", (0, 0)),
                ("Example-windows-x86.zip", (0, 0)), ("Example-win32.zip", (0, 0)),
                ("Example-windows-32.zip", (0, 0)), ("Example-winarm64.zip", (0, 0)),
                ("Example-linux-x86_64.zip", (0, 0)), ("Example-x64.zip", (0, 0)),
                ("Example-macos-arm64.zip", (0, 3)), ("Example-darwin-aarch64.zip", (0, 3)),
                ("Example-mac-intel.zip", (0, 0)), ("Example-win-mac.zip", (0, 0)),
                ("Example-win-x64-arm64.zip", (0, 0)), ("Example-win64-debug.zip", (0, 0)),
                ("Example-universal2.zip", (0, 2)), ("Example-universally.zip", (1, 1)),
                ("Mac-Tools-win-x64.zip", (3, 0)), ("Mac-Tools.zip", (1, 1)),
                ("Example-shadowwin-x64ish.zip", (1, 1)),
            ):
                repo = "Owner/Mac-Tools" if filename.startswith("Mac-Tools") else self.source["repo"]
                asset = {"name": filename, "browser_download_url": f"https://github.com/{repo}/releases/download/v1.2.0/{filename}"}
                with self.subTest(filename=filename):
                    self.assertEqual(tuple(releases.package_rank(asset, repo, target_platform)
                                           for target_platform in ("windows", "mac")), expected)

    def test_windows_native_update_can_select_stage_and_validate_the_addin(self):
        filename = "Example-win-x64.zip"
        data = self.package({**self.info, "supportedOS": "Windows"}, entries={"Example.dll": pe_image()})
        url = f"https://github.com/Owner/Example/releases/download/v1.2.0/{filename}"
        asset = {"name": filename, "browser_download_url": url, "digest": "sha256:" + hashlib.sha256(data).hexdigest()}
        row = {"key": "example", "name": "Example", "version": "1.1.0", "writable": True}
        with (tempfile.TemporaryDirectory() as directory, patch.object(releases, "PLATFORM", "windows"),
              patch.object(inventory, "PLATFORM", "windows")):
            release = releases.release_option({"tag_name": "v1.2.0", "assets": [asset]}, self.source["repo"])
            plan = versions.plan_row(row, self.source, release, versions.LATEST)
            self.assertEqual((plan["direction"], plan["tag"]), ("upgrade", "v1.2.0"))
            self.assertEqual(versions.select_asset(release), {**asset, "compatible": True})
            _, package, entry, _ = self.stage(Path(directory), data, filename=filename)
            self.assertEqual(entry, "Example.dll")
            self.assertEqual(inventory.local_addin(package), self.source["id"])

    def test_stage_accepts_cross_platform_scripts_and_macos_dylib(self):
        with tempfile.TemporaryDirectory() as directory:
            for platform, suffix in (("windows", ".py"), ("windows", ".ts"), ("mac", ".js"), ("mac", ".dylib")):
                filename = "Example" + suffix
                with self.subTest(platform=platform, suffix=suffix), patch.object(releases, "PLATFORM", platform):
                    data = self.package(self.info, entries={filename: b"offline fixture"})
                    _, _, entry, _ = self.stage(Path(directory), data)
                    self.assertEqual(entry, filename)

    def test_folder_and_manifest_stem_can_differ_in_case_on_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = self.package(self.info, folder="EXAMPLE")
            with patch.object(releases, "PLATFORM", "windows"):
                _, package, entry, _ = self.stage(root, data)
                self.assertEqual((package.name, entry), ("EXAMPLE", "Example.py"))
            with patch.object(releases, "PLATFORM", "mac"), self.assertRaises(ValueError):
                self.stage(root, data)

    def test_wrong_platform_or_wrong_dll_image_is_rejected_during_staging(self):
        cases = (
            ("windows", "Example.dylib", b"placeholder"), ("mac", "Example.dll", pe_image()),
            ("windows", "Example.dll", pe_image(machine=0xAA64)),
            ("windows", "Example.dll", pe_image(machine=0x14C)),
            ("windows", "Example.dll", pe_image(flags=0x0002)),
            ("windows", "Example.dll", pe_image(magic=0x10B)),
            ("windows", "Example.dll", b"not a PE image"),
            ("windows", "Other.dll", pe_image()), ("windows", "Example.cpp", b"source"),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for platform, filename, content in cases:
                with self.subTest(platform=platform, filename=filename), patch.object(releases, "PLATFORM", platform):
                    with self.assertRaises(ValueError):
                        self.stage(root, self.package(self.info, entries={filename: content}))
                    self.assertEqual(list(root.iterdir()), [])

    def test_staging_rejects_an_asset_for_another_platform_before_download(self):
        filename = "Example-macos.zip"
        asset = {"name": filename, "digest": "sha256:" + "a" * 64,
                 "browser_download_url": f"https://github.com/Owner/Example/releases/download/v1.2.0/{filename}"}
        with (tempfile.TemporaryDirectory() as directory, patch.object(releases, "PLATFORM", "windows"),
              patch.object(releases, "request") as download):
            with self.assertRaises(ValueError):
                releases.stage(self.source, {"tag": "v1.2.0", "asset": asset}, Path(directory))
            download.assert_not_called()

    def test_discovery_can_inspect_windows_dll_on_mac_but_windows_must_be_declared(self):
        entries = {"Example.dll": pe_image()}
        with tempfile.TemporaryDirectory() as directory, patch.object(releases, "PLATFORM", "mac"):
            root = Path(directory)
            _, _, entry, _ = self.stage(root, self.package(self.info, entries=entries), check_platform=False)
            self.assertEqual(entry, "Example.dll")
            with self.assertRaises(ValueError):
                self.stage(root, self.package({**self.info, "supportedOS": "mac"}, entries=entries), check_platform=False)

    def test_release_keeps_multiple_assets_with_their_compatibility(self):
        repo = self.source["repo"]
        asset_url = f"https://github.com/{repo}/releases/download/v1.2.0/"
        names = ["Example-macos.zip", "Example-universal.zip", "Example-windows.zip", "Example.sha256"]
        check = "sha256:" + "a" * 64
        data = {"tag_name": "v1.2.0", "assets": [
            {"name": name, "browser_download_url": asset_url + name, "digest": check} for name in names
        ]}
        cached = {"tag": "v1.2.0", "identity": {"id": "manifest-id", "iconPaths": [], "author": ""}, "identityChecked": True}
        with patch.object(releases, "github_json", return_value=data), patch.object(releases, "PLATFORM", "mac"):
            result = releases.latest({**self.source, "stars": 13, "description": "GitHub metadata"}, cached)
        self.assertEqual([(a["name"], a["compatible"]) for a in result["assets"]],
                         list(zip(names[:3], [True, True, False])))
        self.assertEqual(result["stars"], 13)
        self.assertEqual(result["description"], "GitHub metadata")
        self.assertTrue(result["isAddin"])

    def test_tagged_manifest_author_is_discovered_when_an_older_cache_has_no_author(self):
        repo = self.source["repo"]
        tag = "v1.2.0"
        digest = "sha256:" + "a" * 64
        stem = self.source["name"]
        name = f"{stem}.zip"
        release = {"tag_name": tag, "assets": [
            {"name": name, "digest": digest, "browser_download_url": f"https://github.com/{repo}/releases/download/{tag}/{name}"}
        ]}
        tree = {"tree": [{"type": "blob", "path": stem + suffix, "sha": "manifest"}
                         for suffix in (".manifest", ".py")]}
        info = {"type": "addin", "author": " Release Author "}
        blob = {"encoding": "base64", "content": base64.b64encode(json.dumps(info).encode()).decode()}
        cached = {"tag": tag, "identityChecked": True, "identity": {"id": "legacy"}}
        with patch.object(releases, "github_json", side_effect=[release, tree, blob, release]) as request:
            refreshed = releases.latest(self.source, cached)
            self.assertEqual(refreshed["identity"]["author"], "Release Author")
            self.assertEqual(releases.latest(self.source, refreshed)["identity"], refreshed["identity"])
        self.assertEqual(request.call_count, 4)

    def test_current_and_historical_assets_are_reranked_from_existing_cache(self):
        repo = self.source["repo"]

        def metadata(tag):
            return {"tag_name": tag, "assets": [
                {"name": name, "digest": "sha256:" + "a" * 64,
                 "browser_download_url": f"https://github.com/{repo}/releases/download/{tag}/{name}"}
                for name in ("Example-windows-x64.zip", "Example-macos.zip")
            ]}

        current, previous = metadata("v1.2.0"), metadata("v1.1.0")
        with patch.object(releases, "PLATFORM", "mac"):
            cached = {**releases.release_option(current, repo), "history": [releases.release_option(previous, repo)],
                      "historyLatestTag": "v1.2.0", "historyChecked": 2000, "identityChecked": True}
        with (patch.object(releases, "PLATFORM", "windows"), patch.object(releases.time, "time", return_value=2060),
              patch.object(releases, "github_json", return_value=current), patch.object(releases, "history") as history):
            restored = releases.refresh_cached_release(cached, repo)
            refreshed = releases.latest(self.source, cached)
            for release in (restored, refreshed):
                self.assertEqual([(asset["name"], asset["compatible"]) for asset in release["assets"]],
                                 [("Example-windows-x64.zip", True), ("Example-macos.zip", False)])
                pinned = versions.select_release(release, "v1.1.0")
                self.assertTrue(versions.select_asset(pinned, "Example-windows-x64.zip"))
                self.assertIsNone(versions.select_asset(pinned, "Example-macos.zip"))
            history.assert_not_called()

    def test_data_repository_with_a_release_zip_is_not_an_addin(self):
        repo = self.source["repo"]
        data = {"tag_name": "v1.2.0", "assets": [{
            "name": "data.zip", "browser_download_url": f"https://github.com/{repo}/releases/download/v1.2.0/data.zip",
            "digest": "sha256:" + "a" * 64,
        }]}
        with (patch.object(releases, "github_json", return_value=data),
              patch.object(releases, "release_identity", return_value={}),
              patch.object(releases, "packaged_identity", return_value={})):
            self.assertFalse(releases.latest(self.source)["isAddin"])


if __name__ == "__main__":
    unittest.main()
