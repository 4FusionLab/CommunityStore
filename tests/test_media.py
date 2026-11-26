"""Offline checks for artwork selection and HTTP cache behavior."""
import os
import struct
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from CommunityStore import media, releases

PNG_HEADER = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR"


class MediaTests(unittest.TestCase):
    def test_markdown_and_html_images_accept_only_repo_paths(self):
        content = """<img src="example.png">
![result](docs/output.jpg)
![duplicate](docs/output.jpg)
![badge](docs/badge.png)
![image](https://elsewhere.example/screenshot.png)
![escape](docs/%2e%2e/escape.png)
"""
        self.assertEqual(media.readme_images(content), (["docs/output.jpg"], ["example.png"]))

    def test_detects_raster_type_and_rejects_large_dimensions(self):
        self.assertEqual(media.image_type(PNG_HEADER + struct.pack(">II", 96, 96)), "png")
        self.assertIsNone(media.image_type(PNG_HEADER + struct.pack(">II", 9000, 96)))
        self.assertIsNone(media.image_type(b"<svg></svg>"))

    def test_only_expected_release_attachments_are_selected_as_artwork(self):
        repo = "Org/Example"
        data = {"tag_name": "v1.0.0", "body": "**Actual notes**", "published_at": "2026-11-25T14:00:00Z",
                "assets": [
                    {"name": "icon.png", "browser_download_url": f"https://github.com/{repo}/releases/download/v1.0.0/icon.png",
                     "updated_at": "changed"},
                    {"name": "preview.jpg", "browser_download_url": "https://github.com/Else/Other/releases/download/v1.0.0/preview.jpg"},
                    {"name": "logo.svg", "browser_download_url": f"https://github.com/{repo}/releases/download/v1.0.0/logo.svg"},
                ]}
        release = releases.release_option(data, repo)
        self.assertEqual(release["notes"], "**Actual notes**")
        self.assertEqual(release["publishedAt"], data["published_at"])
        self.assertEqual(release["imageAssets"], [{"kind": "icon", "url": data["assets"][0]["browser_download_url"],
                                                  "revision": "changed"}])

    def test_expired_cache_revalidates_and_survives_a_network_failure(self):
        url = "https://raw.githubusercontent.com/Org/Example/v1.0.0/README.md"
        with tempfile.TemporaryDirectory() as folder, patch.object(media, "fetch") as fetch:
            cache = media.ArtworkCache(Path(folder))
            fetch.return_value = b"README", '"initial"'
            hosts = {media.RAW_HOST}
            self.assertEqual(cache.read(url, hosts), b"README")
            self.assertEqual(cache.read(url, hosts), b"README")
            self.assertEqual(fetch.call_count, 1)
            entry = next(path for path in Path(folder).iterdir() if not path.suffix)
            os.utime(entry, (1, 1))
            fetch.side_effect = HTTPError(url, 304, "Not modified", Message(), None)
            self.assertEqual(cache.read(url, hosts), b"README")
            self.assertEqual(fetch.call_args.args[-1], '"initial"')
            self.assertGreater(entry.stat().st_mtime, 1)
            os.utime(entry, (1, 1))
            fetch.side_effect = URLError("offline")
            self.assertEqual(cache.read(url, hosts), b"README")

    def test_declared_artwork_is_prioritized_and_deduplicated(self):
        raw = "https://raw.githubusercontent.com/Org/Example/v1.0.0/"
        with tempfile.TemporaryDirectory() as folder, patch.object(media.ArtworkCache, "read") as read:
            read.return_value = b'{"preview":"docs/a.png","screenshots":["docs/a.png","docs/b.jpg","https://other.example/x.png"],"icon":"logo.webp"}'
            cache = media.ArtworkCache(Path(folder))
            self.assertEqual(cache.declared_artwork(raw), (["docs/a.png", "docs/b.jpg"], ["logo.webp"]))

    def test_readme_table_reuses_gallery_images_and_includes_fourth_example(self):
        readme = "# Perfora\n\n## Examples\n\n<table><tr>" + "".join(
            f'<td><img src="docs/{index}.jpg"></td>' for index in range(5)
        ) + "</tr></table>"
        source = {"repo": "Org/Example", "tag": "v1.0.0"}
        with tempfile.TemporaryDirectory() as folder:
            cache = media.ArtworkCache(Path(folder))
            with (patch.object(cache, "readme", return_value=("README.md", readme)),
                  patch.object(cache, "image", side_effect=lambda url, hosts: url.rsplit("/", 1)[-1])):
                card = cache.artwork(source, "en", False)
                detail = cache.artwork(source, "en", True)
        self.assertEqual(card, {"shot0": "0.jpg"})
        self.assertEqual(detail["readmePaths"], {
            "docs/0.jpg": "shot0", "docs/1.jpg": "shot1", "docs/2.jpg": "shot2", "docs/3.jpg": "readmeShot3",
        })
        self.assertEqual(detail["readmeShot3"], "3.jpg")
        self.assertIn("<table>", detail["about"])

    def test_complete_readme_is_published_before_artwork(self):
        opening = "# Add-in\n\nIntroduction before the first section.\n"
        readme = opening + "\n## Usage\n\n![Example](docs/example.png)\n\n" + "Instructions.\n" * 700 + "\n## Final notes"
        source = {"repo": "Org/Example", "tag": "v1.0.0"}
        published = []

        def image(_url, _hosts):
            self.assertEqual(published, [{"about": readme, "readme": "README.ru.md"}])
            return "example.png"

        with tempfile.TemporaryDirectory() as folder:
            cache = media.ArtworkCache(Path(folder))
            with (patch.object(cache, "read", return_value=readme.encode()) as read,
                  patch.object(cache, "image", side_effect=image)):
                detail = cache.artwork(source, "ru", True, publish_readme=published.append)
        read.assert_called_once_with("https://raw.githubusercontent.com/Org/Example/v1.0.0/README.ru.md",
                                     {media.RAW_HOST})
        self.assertNotIn("about", detail)
        self.assertEqual(detail["readmePaths"], {"docs/example.png": "shot0"})
