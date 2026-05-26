"""Tests for the Slack Events API webhook handler."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest

# Patch env before importing handler
os.environ["SLACK_SIGNING_SECRET"] = "test_signing_secret_1234"
os.environ["AGENT_CONFIG_JSON"] = json.dumps({
    "chitra": {"dm_channel": "D_CHITRA"},
    "bhaga": {"dm_channel": "D_BHAGA"},
    "chanakya": {"dm_channel": "D_CHANAKYA"},
})

# Patch Firestore before import so init_app doesn't need real GCP creds
with patch("google.cloud.firestore.Client"):
    import handler

app = handler.app
app.testing = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signature(body: bytes, timestamp: str, secret: str = "test_signing_secret_1234") -> str:
    sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    return "v0=" + hmac.new(
        secret.encode("utf-8"),
        sig_basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _post_event(client, payload: dict, *, secret: str = "test_signing_secret_1234", ts: str | None = None):
    body = json.dumps(payload).encode("utf-8")
    timestamp = ts or str(int(time.time()))
    signature = _make_signature(body, timestamp, secret)
    return client.post(
        "/slack/events",
        data=body,
        content_type="application/json",
        headers={
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": signature,
        },
    )


def _post_command(client, text: str, **extra_form):
    from urllib.parse import urlencode
    form = {"text": text, "command": "/bhaga", **extra_form}
    body = urlencode(form).encode("utf-8")
    timestamp = str(int(time.time()))
    signature = _make_signature(body, timestamp)
    return client.post(
        "/slack/commands",
        data=body,
        content_type="application/x-www-form-urlencoded",
        headers={
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": signature,
        },
    )


# ===========================================================================
# 1. Signing secret verification
# ===========================================================================

class TestSignatureVerification:
    def test_valid_signature(self):
        with app.test_client() as c:
            resp = _post_event(c, {"type": "url_verification", "challenge": "abc"})
            assert resp.status_code == 200

    def test_invalid_signature(self):
        with app.test_client() as c:
            resp = _post_event(c, {"type": "url_verification", "challenge": "abc"}, secret="wrong_secret")
            assert resp.status_code == 403

    def test_expired_timestamp(self):
        with app.test_client() as c:
            stale_ts = str(int(time.time()) - 400)
            resp = _post_event(c, {"type": "url_verification", "challenge": "abc"}, ts=stale_ts)
            assert resp.status_code == 403

    def test_missing_headers(self):
        with app.test_client() as c:
            resp = c.post(
                "/slack/events",
                data=b"{}",
                content_type="application/json",
            )
            assert resp.status_code == 403

    def test_empty_signing_secret(self):
        body = b'{"type":"url_verification","challenge":"x"}'
        assert handler.verify_slack_signature(body, str(int(time.time())), "v0=abc", signing_secret="") is False


# ===========================================================================
# 2. URL verification challenge
# ===========================================================================

class TestUrlVerification:
    def test_challenge_response(self):
        with app.test_client() as c:
            resp = _post_event(c, {"type": "url_verification", "challenge": "test_challenge_xyz"})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["challenge"] == "test_challenge_xyz"

    def test_challenge_empty(self):
        with app.test_client() as c:
            resp = _post_event(c, {"type": "url_verification", "challenge": ""})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["challenge"] == ""


# ===========================================================================
# 3. OTP extraction
# ===========================================================================

class TestOtpExtraction:
    @pytest.mark.parametrize("text,expected", [
        ("123456", "123456"),
        ("  123456  ", "123456"),
        ("the code is 123456", "123456"),
        ("OTP: 123456", "123456"),
        ("pin is 7890", "7890"),
        ("code:654321", "654321"),
        ("Your verification code is 123456.", "123456"),
        ("12345678", "12345678"),
        ("1234", "1234"),
        ("123 456", "123456"),          # spaces in code
        ("12-34-56", "123456"),         # dashes in code
    ])
    def test_valid_otps(self, text, expected):
        assert handler.extract_otp(text) == expected

    @pytest.mark.parametrize("text", [
        "hello world",
        "no code here",
        "",
        "123",                          # too short
    ])
    def test_no_otp(self, text):
        assert handler.extract_otp(text) is None

    def test_long_digits_extracts_trailing(self):
        """A 12-digit string still matches a 6-digit tail via pattern fallback."""
        result = handler.extract_otp("123456789012")
        assert result is not None  # regex finds a 6-digit sub-match


# ===========================================================================
# 4. Agent-aware routing
# ===========================================================================

class TestAgentRouting:
    def setup_method(self):
        handler._init_agent_config()

    def test_channel_to_agent_mapping(self):
        assert handler._CHANNEL_TO_AGENT["D_BHAGA"] == "bhaga"
        assert handler._CHANNEL_TO_AGENT["D_CHITRA"] == "chitra"
        assert handler._CHANNEL_TO_AGENT["D_CHANAKYA"] == "chanakya"

    def test_unknown_channel_ignored(self):
        """Messages from unmapped channels should not route anywhere."""
        assert handler._CHANNEL_TO_AGENT.get("D_UNKNOWN") is None

    @patch.object(handler, "_find_pending_portal_for_agent")
    @patch.object(handler, "_complete_otp")
    def test_otp_routed_to_correct_agent(self, mock_complete, mock_find):
        """OTP in BHAGA's DM channel routes only to bhaga's pending portal."""
        mock_find.return_value = {
            "_doc_id": "bhaga_square",
            "portal": "square",
            "agent": "bhaga",
            "status": "pending",
        }

        with app.test_client() as c:
            _post_event(c, {
                "type": "event_callback",
                "event": {
                    "type": "message",
                    "channel": "D_BHAGA",
                    "user": "U_USER",
                    "text": "123456",
                },
            })

        mock_find.assert_called_once_with("bhaga")
        mock_complete.assert_called_once_with("bhaga_square", "123456")

    @patch.object(handler, "_find_pending_portal_for_agent")
    @patch.object(handler, "_complete_otp")
    def test_otp_not_cross_routed(self, mock_complete, mock_find):
        """OTP in CHITRA's channel must NOT satisfy BHAGA's pending request."""
        mock_find.return_value = None  # no pending for chitra

        with app.test_client() as c:
            _post_event(c, {
                "type": "event_callback",
                "event": {
                    "type": "message",
                    "channel": "D_CHITRA",
                    "user": "U_USER",
                    "text": "654321",
                },
            })

        mock_find.assert_called_once_with("chitra")
        mock_complete.assert_not_called()

    @patch.object(handler, "_find_pending_portal_for_agent")
    @patch.object(handler, "_complete_otp")
    def test_bot_messages_ignored(self, mock_complete, mock_find):
        """Bot messages should be silently dropped."""
        with app.test_client() as c:
            _post_event(c, {
                "type": "event_callback",
                "event": {
                    "type": "message",
                    "channel": "D_BHAGA",
                    "bot_id": "B_BOT",
                    "text": "123456",
                },
            })

        mock_find.assert_not_called()
        mock_complete.assert_not_called()

    @patch.object(handler, "_find_pending_portal_for_agent")
    @patch.object(handler, "_complete_otp")
    def test_no_pending_portal_does_not_write(self, mock_complete, mock_find):
        """If no portal is waiting, OTP is logged but not written."""
        mock_find.return_value = None

        with app.test_client() as c:
            _post_event(c, {
                "type": "event_callback",
                "event": {
                    "type": "message",
                    "channel": "D_BHAGA",
                    "user": "U_USER",
                    "text": "999999",
                },
            })

        mock_find.assert_called_once_with("bhaga")
        mock_complete.assert_not_called()


# ===========================================================================
# 5. Slash command parsing
# ===========================================================================

class TestSlashCommands:
    @patch.object(handler, "_trigger_cloud_run_job")
    def test_refresh_command(self, mock_trigger):
        with app.test_client() as c:
            resp = _post_command(c, "refresh 2025-05-26")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "2025-05-26" in data["text"]
            mock_trigger.assert_called_once_with("2025-05-26")

    @patch.object(handler, "_get_latest_run_summary", return_value=":white_check_mark: All good")
    def test_status_command(self, mock_summary):
        with app.test_client() as c:
            resp = _post_command(c, "status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "All good" in data["text"]

    def test_help_command(self):
        with app.test_client() as c:
            resp = _post_command(c, "")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "Commands" in data["text"]

    def test_unknown_command_shows_help(self):
        with app.test_client() as c:
            resp = _post_command(c, "foobar")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "Commands" in data["text"]


# ===========================================================================
# 6. Health check
# ===========================================================================

class TestHealth:
    def test_health_returns_ok(self):
        with app.test_client() as c:
            resp = c.get("/health")
            assert resp.status_code == 200
            assert resp.get_json()["status"] == "ok"


# ===========================================================================
# 7. Edge cases
# ===========================================================================

class TestEdgeCases:
    def test_bad_json_body(self):
        with app.test_client() as c:
            body = b"not json"
            timestamp = str(int(time.time()))
            signature = _make_signature(body, timestamp)
            resp = c.post(
                "/slack/events",
                data=body,
                content_type="application/json",
                headers={
                    "X-Slack-Request-Timestamp": timestamp,
                    "X-Slack-Signature": signature,
                },
            )
            assert resp.status_code == 400

    def test_event_callback_returns_200(self):
        """Even unhandled event types should return 200 to avoid Slack retries."""
        with app.test_client() as c:
            resp = _post_event(c, {
                "type": "event_callback",
                "event": {"type": "app_mention", "text": "hi"},
            })
            assert resp.status_code == 200

    def test_message_subtype_ignored(self):
        """Edited messages (subtype=message_changed) should be dropped."""
        with app.test_client() as c:
            with patch.object(handler, "_find_pending_portal_for_agent") as mock_find:
                _post_event(c, {
                    "type": "event_callback",
                    "event": {
                        "type": "message",
                        "subtype": "message_changed",
                        "channel": "D_BHAGA",
                        "user": "U_USER",
                        "text": "123456",
                    },
                })
                mock_find.assert_not_called()
