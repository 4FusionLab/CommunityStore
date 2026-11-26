import io
import json
import unittest
from unittest.mock import patch

from CommunityStore import releases, versions


REPO = "Owner/Example"


def metadata(tag, *packages, **fields):
    return {"tag_name": tag, "assets": [
        {"name": name, "browser_download_url": f"https://github.com/{REPO}/releases/download/{tag}/{name}",
         "digest": "sha256:" + "a" * 64} for name in packages
    ], **fields}


class TargetVersionTests(unittest.TestCase):
    def setUp(self):
        self.source = {"repo": REPO}
        self.old = releases.release_option(metadata("v1.0.0", "Example.zip"), REPO)
        self.latest = {**releases.release_option(metadata("v2.0.0", "Example.zip"), REPO),
                       "history": [self.old]}
        self.row = {"key": "/test/Example", "name": "Example", "id": "example",
                    "version": "1.0.0", "writable": True}

    def test_existing_records_default_to_latest_and_pin_is_per_registration(self):
        self.assertEqual(versions.target_for({}, self.row), "Latest")
        preferences = {self.row["key"]: {"id": "example", "target": self.old["tag"]}}
        self.assertEqual(versions.target_for(preferences, self.row), "v1.0.0")
        self.assertEqual(versions.target_for(preferences, {**self.row, "key": "/alias/Example"}), "Latest")
        self.assertEqual(versions.target_for(preferences, {**self.row, "id": "another"}), "Latest")

    def test_latest_upgrades_pin_does_not_jump_and_pin_can_downgrade(self):
        def plan(installed, target):
            return versions.plan_row({**self.row, "version": installed}, self.source, self.latest, target)

        self.assertEqual(plan("1.0.0", "Latest")["direction"], "upgrade")
        self.assertEqual(plan("1.0.0", "v1.0.0")["direction"], "")
        self.assertEqual(versions.newer_stable_tag("1.0.0", self.latest), "v2.0.0")
        self.assertIsNone(versions.newer_stable_tag("2.0.0", self.latest))
        self.assertEqual(plan("3.0.0", "Latest")["direction"], "")
        self.assertEqual(plan("2.0.0", "v1.0.0")["direction"], "downgrade")
        self.assertEqual(plan("1.0.0", "v0.1.0")["direction"], "")
        self.assertIsNone(versions.select_release(self.latest, "v0.1.0"))

    def test_multiple_zips_require_an_explicit_compatible_choice(self):
        release = {"assets": [{"name": "mac.zip", "compatible": True},
                              {"name": "windows.zip", "compatible": False}]}
        self.assertIsNone(versions.select_asset(release))
        self.assertIsNone(versions.select_asset(release, "windows.zip"))
        self.assertEqual(versions.select_asset(release, "mac.zip"), release["assets"][0])
        self.assertEqual(versions.select_asset(self.old), self.old["assets"][0])

    def test_folder_without_id_requires_an_exact_github_origin(self):
        folder = {**self.row, "id": "", "sourceType": "folder", "checkout": True, "writable": False}
        plan = versions.plan_row(folder, self.source, self.latest, versions.LATEST)
        self.assertEqual(plan["reason"].key, "versions.folder_identity_missing")
        plan = versions.plan_row({**folder, "github": REPO.lower()}, self.source, self.latest, versions.LATEST)
        self.assertEqual(plan["direction"], "upgrade")
        self.assertTrue(plan["fromFolder"])

    def test_latest_falls_back_to_compatible_stable_history_across_pages(self):
        unavailable = metadata("v4.0.0", "Example-linux.zip")
        prerelease = metadata("v3.0.0", "Example.zip", prerelease=True)
        current = metadata("v2.0.0", "Example.zip")
        draft = metadata("v9.0.0", "Example.zip", draft=True)

        def response(items, link=""):
            body = io.BytesIO(json.dumps(items).encode())
            body.headers = {"Link": link}
            return body

        source = {**self.source, "name": "Example", "topics": ["fusion-addin"]}
        with (patch.object(releases, "github_json", return_value=unavailable),
              patch.object(releases, "release_identity", return_value={"id": "example"}),
              patch.object(releases, "request", side_effect=[
                  response([draft, prerelease, unavailable], '<https://api.github.com>; rel="next"'),
                  response([current]),
              ]) as request):
            result = releases.latest(source)
        self.assertEqual(result["tag"], "v2.0.0")
        self.assertEqual([item["tag"] for item in versions.options(result)], ["v2.0.0"])
        self.assertEqual(versions.select_release(result, "Latest")["tag"], "v2.0.0")
        self.assertIsNone(versions.select_release(result, "v3.0.0"))
        self.assertEqual(versions.newer_stable_tag("2.0.0", result), "v4.0.0")
        self.assertTrue(request.call_args_list[1].args[0].endswith("page=2"))

    def test_version_history_is_reused_until_stale_and_can_be_refreshed_explicitly(self):
        data = metadata("v2.0.0", "Example.zip")
        source = {**self.source, "name": "Example"}
        with (patch.object(releases, "github_json", return_value=data),
              patch.object(releases, "release_identity", return_value={"id": "example"}),
              patch.object(releases.time, "time", return_value=2000) as clock,
              patch.object(releases, "history", return_value=[self.old]) as history):
            cached = releases.latest(source, with_history=True)
            self.assertEqual(cached["history"], [self.old])
            restored = json.loads(json.dumps(cached))
            clock.return_value += 60
            self.assertEqual(releases.latest(source, restored), cached)
            history.assert_called_once_with(REPO)
            refreshed = releases.latest(source, restored, with_history=True)
            self.assertEqual(refreshed["historyChecked"], 2060)
            self.assertEqual(history.call_count, 2)
            clock.return_value += releases.HISTORY_TTL
            self.assertEqual(releases.latest(source, refreshed)["historyChecked"], clock.return_value)
            self.assertEqual(history.call_count, 3)
