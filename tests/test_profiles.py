"""Profile schema and conflict handling without a Fusion session."""
import unittest

from CommunityStore import profiles


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.repo = "Example/Panel"
        self.item = {"repo": self.repo, "manifestId": "panel-id", "installTarget": "v1.0.0", "updateTarget": "Latest"}
        self.release = {"tag": "v1.0.0", "identity": {"id": "panel-id", "addinName": "Panel"},
                        "assets": [{"name": "compatible.zip", "compatible": True},
                                   {"name": "other-os.zip", "compatible": False}]}
        self.row = {"name": "Panel", "version": "2.0.0", "key": "local", "visible": True, "writable": True,
                    "id": "panel-id", "sourceType": "community_store", "startup": True}
        self.settings = {"installedSources": {"local": {"id": "panel-id", "repo": self.repo}}}

    def plan(self, *rows):
        return profiles.plan_item(self.item, self.release, list(rows), self.settings)

    def test_platform_choice_conflicts_and_missing_pins(self):
        self.assertEqual(self.plan()["assets"], ["compatible.zip"])
        self.assertEqual(self.plan(self.row)["action"], "downgrade")
        for rows in ([self.row, self.row], [dict(self.row, visible=False)], [dict(self.row, id="other")],
                     [dict(self.row, sourceType="folder")], [dict(self.row, writable=False)]):
            self.assertEqual(self.plan(*rows)["action"], "skip")
        self.settings["installedSources"]["local"]["repo"] = "Other/Panel"
        self.assertEqual(self.plan(self.row)["action"], "skip")
        self.item["installTarget"] = "v0.1.0"
        self.assertEqual(self.plan()["action"], "skip")
        self.item["installTarget"] = "Latest"
        self.release["assets"][0]["compatible"] = False
        self.assertEqual(self.plan()["action"], "skip")

    def test_unknown_fields_duplicates_and_self_are_rejected(self):
        doc = {"format": profiles.FORMAT, "name": "Team", "kind": "collection", "schemaVersion": 1}
        for items in ([dict(self.item, filename="local")], [self.item, self.item],
                      [dict(self.item, manifestId=profiles.PRODUCT_ID)], [dict(self.item, repo="foo/..")],
                      [dict(self.item, runOnStartup=False)]):
            with self.subTest(items=items), self.assertRaises(ValueError):
                profiles.validate(dict(doc, addins=items))
