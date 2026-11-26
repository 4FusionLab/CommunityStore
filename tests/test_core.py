"""Verify version and source rules without Autodesk Fusion."""
import unittest

from CommunityStore.core import github_owner, version


class CoreTests(unittest.TestCase):
    def test_repo_url_subscribes_to_the_owner(self):
        for url in ("https://github.com/Owner", "https://github.com/Owner/Tool/releases",
                    "github.com/Owner/Other.git", "https://github.com/orgs/Owner/repositories"):
            with self.subTest(url=url):
                self.assertEqual(github_owner(url), "Owner")

    def test_rejects_a_source_outside_github(self):
        with self.assertRaises(ValueError):
            github_owner("https://example.com/Owner/Tool")

    def test_compares_numeric_versions_and_skips_prereleases(self):
        self.assertGreater(version("v1.10.0"), version("1.9"))
        self.assertIsNone(version("v2.0.0-rc.1"))

    def test_four_part_release_versions(self):
        self.assertEqual(version("v2.6.0.0"), version("2.6"))
        self.assertEqual(version("2.6.0"), version("2.6.0.0+build.7"))
        self.assertGreater(version("2.6.0.10"), version("2.6.0.9"))
        self.assertLess(version("2.6.0.10"), version("2.6.1"))
        self.assertIsNone(version("2.6.0.1.2"))
        self.assertIsNone(version("v2.6.0.1-rc.1"))
