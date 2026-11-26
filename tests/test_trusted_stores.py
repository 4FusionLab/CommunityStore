"""Validate the public list of default GitHub sources offline."""
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from CommunityStore import trusted_stores


class TrustedStoresTests(unittest.TestCase):
    def test_public_config_is_valid_and_outside_shipped_paths(self):
        root = Path(__file__).resolve().parents[1]
        data = json.loads((root / trusted_stores.FILENAME).read_text(encoding="utf-8"))
        self.assertIsInstance(trusted_stores.parse(data), tuple)
        self.assertFalse(any(Path(trusted_stores.FILENAME).match(path)
                             for path in (root / ".github/release-paths.txt").read_text().splitlines()
                             if path and not path.startswith("#")))

    def test_parse_deduplicates_and_rejects_malformed_documents_as_a_whole(self):
        self.assertEqual(trusted_stores.parse({"schemaVersion": 1, "owners": ["Example", "example", "Other"]}),
                         ("Example", "Other"))
        self.assertEqual(trusted_stores.parse({"schemaVersion": 1, "owners": []}), ())
        for data in (
            None, {"schemaVersion": True, "owners": []}, {"schemaVersion": 2, "owners": []},
            {"schemaVersion": 1, "owners": "Example"}, {"schemaVersion": 1, "owners": ["Example", 7]},
            {"schemaVersion": 1, "owners": ["Example", "https://github.com/Other"]},
            {"schemaVersion": 1, "owners": ["Example"] * (trusted_stores.MAX_OWNERS + 1)},
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                trusted_stores.parse(data)

    def test_fetch_uses_public_raw_endpoint_and_bounds_input(self):
        data = b'{"schemaVersion":1,"owners":["NewOwner"]}'

        def response(body, url=trusted_stores.URL):
            stream = io.BytesIO(body)
            stream.geturl = lambda: url
            return stream

        with patch.object(trusted_stores, "urlopen", return_value=response(data)) as opener:
            self.assertEqual(trusted_stores.fetch(), ("NewOwner",))
            request = opener.call_args.args[0]
            self.assertEqual(request.full_url, trusted_stores.URL)
            self.assertIsNone(request.get_header("Authorization"))
        for invalid in (
            response(data, "https://example.com/trusted-stores.json"),
            response(b" " * trusted_stores.MAX_BYTES + data),
        ):
            with patch.object(trusted_stores, "urlopen", return_value=invalid), self.assertRaises(ValueError):
                trusted_stores.fetch()
