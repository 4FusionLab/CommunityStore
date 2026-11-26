import io
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request
from uuid import uuid4

from CommunityStore import credentials, github_auth, media, releases


class DeviceFlowTests(unittest.TestCase):
    def setUp(self):
        self.client_id = "local_test_app"
        self.bearer = uuid4().hex
        self.device = {
            "device_code": uuid4().hex, "user_code": "ABCD-1234",
            "verification_uri": github_auth.DEVICE_PAGE, "interval": 5, "expires_in": 60,
        }
        self.token_response = {"access_token": self.bearer, "scope": "", "token_type": "bearer"}
        self.now = 100
        self.cancelled = Mock()
        self.cancelled.is_set.return_value = False
        self.cancelled.wait.side_effect = self.advance
        self.show_code = Mock()

    def advance(self, seconds):
        self.now += seconds
        return False

    def authorize(self, *responses):
        with (patch.object(github_auth.time, "monotonic", side_effect=lambda: self.now),
              patch.object(github_auth, "_post", side_effect=responses) as post):
            self.post = post
            return github_auth.authorize(self.client_id, self.cancelled, self.show_code)

    def test_device_flow_waits_and_requests_no_scopes_or_client_secret(self):
        result = self.authorize(self.device, {"error": "authorization_pending"},
                                {"error": "slow_down", "interval": 12}, self.token_response)
        self.assertEqual(result, self.bearer)
        self.show_code.assert_called_once_with(self.device["user_code"])
        self.assertEqual([call.args[0] for call in self.cancelled.wait.call_args_list], [5, 5, 12])
        self.assertEqual(self.post.call_args_list[0].kwargs, {"client_id": self.client_id, "scope": ""})
        for call in self.post.call_args_list[1:]:
            self.assertEqual(set(call.kwargs), {"client_id", "device_code", "grant_type"})

    def test_cancel_stops_polling_and_expired_codes_are_never_polled(self):
        self.cancelled.wait.side_effect = lambda _: True
        self.assertIsNone(self.authorize(self.device))
        self.post.assert_called_once()
        self.cancelled.wait.side_effect = self.advance
        with self.assertRaises(github_auth.AuthorizationError) as error:
            self.authorize({**self.device, "expires_in": 3})
        self.assertEqual(error.exception.args[0].key, "github.expired")
        self.post.assert_called_once()

    def test_unexpected_browser_target_and_excess_scopes_are_rejected(self):
        with self.assertRaises(github_auth.AuthorizationError):
            self.authorize({**self.device, "verification_uri": "https://example.test/device"})
        self.show_code.assert_not_called()
        with self.assertRaises(github_auth.AuthorizationError) as error:
            self.authorize(self.device, {**self.token_response, "scope": "public_repo"})
        self.assertEqual(error.exception.args[0].key, "github.scope_invalid")
        self.assertNotIn(self.bearer, str(error.exception))

    def test_denied_authorization_returns_a_safe_message(self):
        with self.assertRaises(github_auth.AuthorizationError) as error:
            self.authorize(self.device, {"error": "access_denied", "error_description": self.bearer})
        self.assertEqual(error.exception.args[0].key, "github.denied")
        self.assertNotIn(self.bearer, str(error.exception))

    def test_device_flow_disabled_http_response_reports_app_configuration(self):
        disabled = HTTPError(f"{github_auth.DEVICE_PAGE}/code", 400, "Bad Request", {},
                             io.BytesIO(b'{"error": "device_flow_disabled"}'))
        with patch.object(github_auth, "build_opener") as opener:
            opener.return_value.open.side_effect = disabled
            with self.assertRaises(github_auth.AuthorizationError) as error:
                github_auth.authorize(self.client_id, self.cancelled, self.show_code)
        self.assertEqual(error.exception.args[0].key, "github.app_invalid")


class CatalogSessionTests(unittest.TestCase):
    METADATA = "https://api.github.com/repos/Owner/Example/releases/latest"

    def setUp(self):
        self.bearer = uuid4().hex
        self.session = releases.GitHubSession(self.bearer)

    def test_profile_uses_current_token_and_returns_only_public_display_fields(self):
        body = b'{"login":"octocat","name":" Mona ","avatar_url":"https://avatars.githubusercontent.com/u/1?v=4","email":"private"}'
        response = io.BytesIO(body)
        response.headers = {}
        with patch.object(releases, "_open_metadata", return_value=response) as open_profile:
            profile = releases.authenticated_profile(self.session)
        self.assertEqual(profile, {"login": "octocat", "name": "Mona",
                                   "avatar": "https://avatars.githubusercontent.com/u/1?v=4"})
        request = open_profile.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.github.com/user")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + self.bearer)

    def test_independent_rate_pauses_in_both_directions(self):
        response = SimpleNamespace(headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1200"})
        with (patch.object(releases, "_retry_at", 1100),
              patch.object(releases.time, "time", return_value=1000) as clock,
              patch.object(releases, "_open_metadata", return_value=response) as signed,
              patch.object(releases, "urlopen", return_value=SimpleNamespace(headers={})) as anonymous):
            with self.assertRaises(releases.RateLimited):
                releases.request(self.METADATA, releases.GITHUB_JSON)
            with releases.use_session(self.session):
                self.assertIs(releases.request(self.METADATA, releases.GITHUB_JSON), response)
                self.assertEqual(signed.call_args.args[0].get_header("Authorization"), "Bearer " + self.bearer)
            self.assertEqual(releases.retry_at(), 1100)
            self.assertEqual(releases.retry_at(self.session), 1200)
            clock.return_value = 1100
            with releases.use_session(self.session), self.assertRaises(releases.RateLimited):
                releases.request(self.METADATA, releases.GITHUB_JSON)
            releases.request(self.METADATA, releases.GITHUB_JSON)
            self.assertIsNone(anonymous.call_args.args[0].get_header("Authorization"))
            signed.assert_called_once()
            anonymous.assert_called_once()

    def test_asset_and_image_requests_are_anonymous_even_during_catalog_work(self):
        urls = [
            ("https://github.com/Owner/Example/releases/download/v1.0/Example.zip", "application/octet-stream"),
            ("https://api.github.com/repos/Owner/Example/releases/assets/123", "application/octet-stream"),
            ("https://raw.githubusercontent.com/Owner/Example/main/example.png", releases.GITHUB_JSON),
        ]
        with (patch.object(releases, "_retry_at", 0),
              patch.object(releases, "urlopen", return_value=SimpleNamespace(headers={})) as anonymous,
              patch.object(releases, "_open_metadata") as signed,
              releases.use_session(self.session)):
            for url, accept in urls:
                releases.request(url, accept)
        signed.assert_not_called()
        self.assertEqual(anonymous.call_count, len(urls))
        for call in anonymous.call_args_list:
            self.assertIsNone(call.args[0].get_header("Authorization"))

        with patch.object(media, "build_opener") as opener, releases.use_session(self.session):
            response = opener.return_value.open.return_value.__enter__.return_value
            response.headers.get_content_type.return_value = "image/png"
            response.read.return_value = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            self.assertIsNotNone(media.fetch(urls[-1][0], {"raw.githubusercontent.com"}, 1024, True))
            self.assertIsNone(opener.return_value.open.call_args.args[0].get_header("Authorization"))

    def test_authenticated_redirects_stay_on_metadata_endpoints(self):
        original = Request(self.METADATA)
        original.add_unredirected_header("Authorization", "Bearer " + self.bearer)
        redirect = releases.MetadataRedirect()
        moved = redirect.redirect_request(original, None, 301, "", {},
                                          "https://api.github.com/repositories/123/releases/latest")
        self.assertEqual(moved.get_header("Authorization"), original.get_header("Authorization"))
        for url in ("https://example.test/releases/latest", "http://api.github.com/repositories/123/releases/latest",
                    "https://api.github.com/repos/Owner/Example/releases/assets/123"):
            self.assertIsNone(redirect.redirect_request(original, None, 302, "", {}, url))

    def test_a_401_ends_only_the_current_session(self):
        rejected = HTTPError(self.METADATA, 401, self.bearer, {}, io.BytesIO())
        with (patch.object(releases, "_retry_at", 0),
              patch.object(releases, "_open_metadata", side_effect=rejected) as signed,
              patch.object(releases, "urlopen", return_value=SimpleNamespace(headers={})) as guest):
            with releases.use_session(self.session):
                for _ in range(2):
                    with self.assertRaises(releases.SessionEnded):
                        releases.request(self.METADATA, releases.GITHUB_JSON)
            self.assertTrue(self.session.ended.is_set())
            self.assertEqual(releases.retry_at(self.session), 0)
            releases.request(self.METADATA, releases.GITHUB_JSON)
            signed.assert_called_once()
            guest.assert_called_once()


class CredentialStoreTests(unittest.TestCase):
    def test_vault_selection_and_no_unsupported_platform_fallback(self):
        for platform, name in (("darwin", "MacKeychain"), ("win32", "WindowsCredentials")):
            with (patch.object(credentials.sys, "platform", platform),
                  patch.object(credentials, name) as factory):
                self.assertIs(credentials.open_store("app"), factory.return_value)
                factory.assert_called_once_with("app")
        with patch.object(credentials.sys, "platform", "linux"), self.assertRaises(credentials.CredentialError):
            credentials.open_store("app")

    @unittest.skipUnless(
        sys.platform == "win32" and os.environ.get("COMMUNITY_STORE_TEST_WINDOWS_VAULT") == "1",
        "Requires opt-in access to the Windows user vault",
    )
    def test_windows_vault_round_trip_with_a_unique_test_entry(self):
        account = "test-" + uuid4().hex
        vault = credentials.open_store(account)
        self.addCleanup(vault.delete)
        self.assertIsNone(vault.load())
        for value in ("first-test-value", "second-test-value"):
            vault.save(value)
            self.assertEqual(credentials.open_store(account).load(), value)
        vault.delete()
        vault.delete()
        self.assertIsNone(vault.load())
