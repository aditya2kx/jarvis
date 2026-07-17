#!/usr/bin/env python3
"""Unit tests for skills/square_api/auth — token parsing, load, refresh, save."""

from __future__ import annotations

import datetime
import json
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.square_api.auth import (
    _parse_expires_at,
    _now_utc,
    REFRESH_WINDOW,
    SquareAuthError,
)


class TestParseExpiresAt(unittest.TestCase):
    def test_rfc3339_utc_z(self):
        dt = _parse_expires_at("2026-07-11T00:00:00Z")
        self.assertEqual(dt.tzinfo, datetime.timezone.utc)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 7)

    def test_rfc3339_with_offset(self):
        dt = _parse_expires_at("2026-07-11T00:00:00+00:00")
        self.assertEqual(dt.tzinfo, datetime.timezone.utc)

    def test_empty_string_treated_as_expired(self):
        dt = _parse_expires_at("")
        self.assertLess(dt, _now_utc())

    def test_none_string_treated_as_expired(self):
        dt = _parse_expires_at(None)
        self.assertLess(dt, _now_utc())

    def test_garbage_treated_as_expired(self):
        dt = _parse_expires_at("not-a-date")
        self.assertLess(dt, _now_utc())


_VALID_SECRET = json.dumps({
    "application_id": "sq0idp-test",
    "application_secret": "secret",
    "access_token": "EAAAtest",
    "refresh_token": "refresh_test",
    "expires_at": "2099-01-01T00:00:00Z",
    "merchant_id": "MERCHANT1",
})

_NEAR_EXPIRY_SECRET = json.dumps({
    "application_id": "sq0idp-test",
    "application_secret": "secret",
    "access_token": "EAAAtest",
    "refresh_token": "refresh_test",
    # 2 days from now — within REFRESH_WINDOW (7 days)
    "expires_at": (
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=2)
    ).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "merchant_id": "MERCHANT1",
})


class TestGetAccessToken(unittest.TestCase):
    def test_returns_token_when_not_near_expiry(self):
        from skills.square_api import auth as auth_mod
        with patch.object(auth_mod.cred_registry, "get_secret", return_value=_VALID_SECRET):
            token = auth_mod.get_access_token("palmetto")
        self.assertEqual(token, "EAAAtest")

    def test_refreshes_when_near_expiry(self):
        from skills.square_api import auth as auth_mod

        refreshed_secret = json.loads(_NEAR_EXPIRY_SECRET)
        refreshed_secret["access_token"] = "EAAAnew"
        refreshed_secret["expires_at"] = "2099-06-01T00:00:00Z"

        with (
            patch.object(auth_mod.cred_registry, "get_secret", return_value=_NEAR_EXPIRY_SECRET),
            patch.object(auth_mod, "save_oauth_secret") as mock_save,
            patch.object(auth_mod, "_post_token", return_value={
                "access_token": "EAAAnew",
                "expires_at": "2099-06-01T00:00:00Z",
                "refresh_token": "refresh_test",
            }) as mock_post,
        ):
            token = auth_mod.get_access_token("palmetto")

        self.assertEqual(token, "EAAAnew")
        mock_post.assert_called_once()
        mock_save.assert_called_once()

    def test_raises_on_missing_secret(self):
        from skills.square_api import auth as auth_mod
        with patch.object(auth_mod.cred_registry, "get_secret", side_effect=RuntimeError("not found")):
            with self.assertRaises(SquareAuthError):
                auth_mod.get_access_token("palmetto")

    def test_raises_on_invalid_json(self):
        from skills.square_api import auth as auth_mod
        with patch.object(auth_mod.cred_registry, "get_secret", return_value="not-json"):
            with self.assertRaises(SquareAuthError):
                auth_mod.get_access_token("palmetto")

    def test_raises_on_missing_fields(self):
        from skills.square_api import auth as auth_mod
        incomplete = json.dumps({"application_id": "sq0idp-x"})
        with patch.object(auth_mod.cred_registry, "get_secret", return_value=incomplete):
            with self.assertRaises(SquareAuthError) as ctx:
                auth_mod.get_access_token("palmetto")
        self.assertIn("missing required fields", str(ctx.exception))


class TestSaveOauthSecret(unittest.TestCase):
    def test_keychain_backend_calls_add_keychain(self):
        from skills.square_api import auth as auth_mod
        data = json.loads(_VALID_SECRET)
        with (
            patch.dict(os.environ, {"BHAGA_SECRETS_BACKEND": "keychain"}),
            patch.object(auth_mod.cred_registry, "lookup", return_value=None),
            patch.object(auth_mod.cred_registry, "add_keychain") as mock_add,
        ):
            auth_mod.save_oauth_secret("palmetto", data)
        mock_add.assert_called_once()

    def test_gcp_backend_calls_secret_manager(self):
        from skills.square_api import auth as auth_mod
        data = json.loads(_VALID_SECRET)
        mock_client = MagicMock()

        # Patch the SecretManagerServiceClient directly inside the google.cloud.secretmanager
        # module (which is already loaded) so the `from google.cloud import secretmanager`
        # inside save_oauth_secret gets a mock client.
        import google.cloud.secretmanager as gcm
        with (
            patch.dict(os.environ, {"BHAGA_SECRETS_BACKEND": "gcp"}),
            patch.object(gcm, "SecretManagerServiceClient", return_value=mock_client),
        ):
            auth_mod.save_oauth_secret("palmetto", data)
        mock_client.add_secret_version.assert_called_once()
        mock_client.get_secret.assert_not_called()
        mock_client.create_secret.assert_not_called()

    def test_gcp_backend_raises_on_add_failure(self):
        from skills.square_api import auth as auth_mod
        data = json.loads(_VALID_SECRET)
        mock_client = MagicMock()
        mock_client.add_secret_version.side_effect = RuntimeError("denied")
        import google.cloud.secretmanager as gcm
        with (
            patch.dict(os.environ, {"BHAGA_SECRETS_BACKEND": "gcp"}),
            patch.object(gcm, "SecretManagerServiceClient", return_value=mock_client),
        ):
            with self.assertRaises(auth_mod.SquareAuthError) as ctx:
                auth_mod.save_oauth_secret("palmetto", data)
        self.assertIn("secretVersionAdder", str(ctx.exception))
        mock_client.create_secret.assert_not_called()


if __name__ == "__main__":
    unittest.main()
