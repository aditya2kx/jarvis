"""Tests for the Slack Events API webhook handler."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from unittest.mock import MagicMock, call, patch

import pytest

# Patch env before importing handler
os.environ["SLACK_SIGNING_SECRET"] = "test_signing_secret_1234"
os.environ["AGENT_CONFIG_JSON"] = json.dumps({
    "chitra": {"dm_channel": "D_CHITRA"},
    # bhaga owns TWO DM channels: the local bot's DM (dm_channel) and the
    # separate cloud "bhaga-cloud" bot's DM (cloud_dm_channel). Both must route
    # to the "bhaga" agent (the cloud nightly job checkpoints under that name).
    "bhaga": {"dm_channel": "D_BHAGA", "cloud_dm_channel": "D_BHAGA_CLOUD"},
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
    # Always include a response_url so async workers can call _post_response_url.
    # Tests that want to capture the follow-up should monkeypatch _post_response_url.
    form = {
        "text": text,
        "command": "/bhaga",
        "response_url": "https://hooks.slack.com/commands/test/response_url",
        **extra_form,
    }
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


# ---------------------------------------------------------------------------
# Test helpers for the async ack pattern
# ---------------------------------------------------------------------------

def _sync_dispatch(monkeypatch):
    """Patch _dispatch_async to run synchronously; capture _post_response_url calls.

    Returns a list that accumulates every (payload) dict passed to
    _post_response_url, so tests can assert on the follow-up content.

    Usage:
        follow_ups = _sync_dispatch(monkeypatch)
        # … make the HTTP request …
        assert "recompute" in follow_ups[0]["text"]
    """
    posted: list[dict] = []

    def _capture_post(url: str, payload: dict) -> None:
        posted.append(payload)

    monkeypatch.setattr(handler, "_dispatch_async", lambda fn, *a: fn(*a))
    monkeypatch.setattr(handler, "_post_response_url", _capture_post)
    return posted


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

    def test_cloud_dm_channel_maps_to_same_agent(self):
        """The cloud bhaga-cloud bot's DM must resolve to the SAME 'bhaga' agent.

        Regression for the cloud OTP-resume routing bug: the nightly job posts
        its READY handshake from a separate bot whose DM channel is distinct
        from the local bot's; both must map to 'bhaga'.
        """
        assert handler._CHANNEL_TO_AGENT["D_BHAGA_CLOUD"] == "bhaga"
        assert handler._CHANNEL_TO_AGENT["D_BHAGA"] == "bhaga"

    def test_extra_dm_channels_list_supported(self):
        """An agent may also declare additional channels via a `dm_channels` list."""
        cfg = {"x": {"dm_channel": "D_X", "dm_channels": ["D_X2", "D_X3"]}}
        with patch.dict(os.environ, {"AGENT_CONFIG_JSON": json.dumps(cfg)}):
            handler._init_agent_config()
            assert handler._CHANNEL_TO_AGENT["D_X"] == "x"
            assert handler._CHANNEL_TO_AGENT["D_X2"] == "x"
            assert handler._CHANNEL_TO_AGENT["D_X3"] == "x"
        handler._init_agent_config()  # restore module-level config

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
# 4b. READY-handshake routing (two-step OTP availability, cloud half)
# ===========================================================================


class _FakeDoc:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return dict(self._data)


class _FakeCollection:
    def __init__(self, docs):
        self._docs = docs  # {doc_id: data}

    def stream(self):
        return [_FakeDoc(i, d) for i, d in self._docs.items()]

    def document(self, doc_id):
        coll = self

        class _Ref:
            def set(self, data, merge=False):
                if merge and doc_id in coll._docs:
                    coll._docs[doc_id].update(data)
                else:
                    coll._docs[doc_id] = dict(data)

        return _Ref()


class _FakeDb:
    def __init__(self, runs, sandbox_runs=None):
        self._runs = _FakeCollection(runs)
        # SANDBOX_RUNS_COLLECTION defaults to "" (sandbox scan OFF); tests that
        # exercise sandbox precedence monkeypatch it to "sandbox_runs" explicitly.
        self._sandbox = _FakeCollection(sandbox_runs or {})

    def collection(self, name):
        if name == "sandbox_runs":
            return self._sandbox
        assert name == "runs"
        return self._runs


class TestIsReadyReply:
    @pytest.mark.parametrize("text", ["ready", "READY", "ok", "go", "yes", "ready to go", "ok!"])
    def test_ready(self, text):
        assert handler.is_ready_reply(text) is True

    @pytest.mark.parametrize("text", ["123456", "no", "later", "", "status"])
    def test_not_ready(self, text):
        assert handler.is_ready_reply(text) is False


class TestFindPendingOtpRun:
    def test_finds_newest_unready_for_agent(self):
        runs = {
            "2026-05-27": {"pending_otp": {
                "agent": "bhaga", "ready_received": False,
                "requested_at": "2026-05-27T21:00:00-05:00", "portals": ["Square"]}},
            "2026-05-28": {"pending_otp": {
                "agent": "bhaga", "ready_received": False,
                "requested_at": "2026-05-28T21:00:00-05:00", "portals": ["Square", "ADP"]}},
        }
        with patch.object(handler, "db", _FakeDb(runs)):
            found = handler._find_pending_otp_run("bhaga")
        assert found is not None
        assert found["date"] == "2026-05-28"

    def test_skips_already_ready(self):
        runs = {"2026-05-28": {"pending_otp": {
            "agent": "bhaga", "ready_received": True,
            "requested_at": "2026-05-28T21:00:00-05:00", "portals": ["Square"]}}}
        with patch.object(handler, "db", _FakeDb(runs)):
            assert handler._find_pending_otp_run("bhaga") is None

    def test_skips_other_agent(self):
        runs = {"2026-05-28": {"pending_otp": {
            "agent": "chitra", "ready_received": False,
            "requested_at": "2026-05-28T21:00:00-05:00", "portals": ["Square"]}}}
        with patch.object(handler, "db", _FakeDb(runs)):
            assert handler._find_pending_otp_run("bhaga") is None

    def test_mark_ready_sets_flag(self):
        runs = {"2026-05-28": {"pending_otp": {
            "agent": "bhaga", "ready_received": False,
            "requested_at": "2026-05-28T21:00:00-05:00", "portals": ["Square"]}}}
        db = _FakeDb(runs)
        with patch.object(handler, "db", db):
            handler._mark_otp_ready("2026-05-28", runs["2026-05-28"]["pending_otp"])
        assert runs["2026-05-28"]["pending_otp"]["ready_received"] is True


class TestReadyEventRouting:
    @patch.object(handler, "_trigger_cloud_run_job")
    @patch.object(handler, "_mark_otp_ready")
    @patch.object(handler, "_find_pending_portal_for_agent")
    def test_ready_reply_triggers_fresh_execution(self, mock_find_otp, mock_mark, mock_trigger):
        """A READY reply marks the checkpoint ready and triggers a new job."""
        with patch.object(
            handler, "_find_pending_otp_run",
            return_value={"date": "2026-05-28", "pending_otp": {"agent": "bhaga"}},
        ):
            with app.test_client() as c:
                _post_event(c, {
                    "type": "event_callback",
                    "event": {
                        "type": "message",
                        "channel": "D_BHAGA",
                        "user": "U_USER",
                        "text": "ready",
                    },
                })
        mock_mark.assert_called_once()
        mock_trigger.assert_called_once_with("2026-05-28")
        # A READY word is not an OTP code → no OTP routing.
        mock_find_otp.assert_not_called()

    @patch.object(handler, "_trigger_cloud_run_job")
    @patch.object(handler, "_mark_otp_ready")
    @patch.object(handler, "_find_pending_portal_for_agent")
    def test_ready_on_cloud_dm_channel_resumes_bhaga(self, mock_find_otp, mock_mark, mock_trigger):
        """A READY arriving on the CLOUD bhaga-cloud DM resolves to agent=bhaga.

        Regression for the cloud OTP-resume routing bug: the operator's READY
        landed on D_BHAGA_CLOUD (the cloud bot's DM), which previously mapped to
        no agent and was dropped. It must now route to bhaga's pending run.
        """
        with patch.object(
            handler, "_find_pending_otp_run",
            return_value={"date": "2026-05-28", "pending_otp": {"agent": "bhaga"}},
        ) as mock_find_run:
            with app.test_client() as c:
                _post_event(c, {
                    "type": "event_callback",
                    "event": {
                        "type": "message",
                        "channel": "D_BHAGA_CLOUD",
                        "user": "U_USER",
                        "text": "READY",
                    },
                })
            mock_find_run.assert_called_once_with("bhaga")
        mock_mark.assert_called_once()
        mock_trigger.assert_called_once_with("2026-05-28")

    @patch.object(handler, "_complete_otp")
    @patch.object(handler, "_find_pending_portal_for_agent")
    def test_code_on_cloud_dm_channel_routes_to_bhaga(self, mock_find, mock_complete):
        """A numeric code on the cloud DM channel routes to bhaga's pending portal."""
        mock_find.return_value = {"_doc_id": "bhaga_square", "portal": "square"}
        with app.test_client() as c:
            _post_event(c, {
                "type": "event_callback",
                "event": {
                    "type": "message",
                    "channel": "D_BHAGA_CLOUD",
                    "user": "U_USER",
                    "text": "123456",
                },
            })
        mock_find.assert_called_once_with("bhaga")
        mock_complete.assert_called_once_with("bhaga_square", "123456")

    @patch.object(handler, "_trigger_cloud_run_job")
    @patch.object(handler, "_find_pending_otp_run", return_value=None)
    def test_ready_reply_no_pending_does_not_trigger(self, mock_find, mock_trigger):
        with app.test_client() as c:
            _post_event(c, {
                "type": "event_callback",
                "event": {
                    "type": "message",
                    "channel": "D_BHAGA",
                    "user": "U_USER",
                    "text": "ready",
                },
            })
        mock_trigger.assert_not_called()

    @patch.object(handler, "_trigger_cloud_run_job")
    @patch.object(handler, "_complete_otp")
    @patch.object(handler, "_find_pending_portal_for_agent")
    def test_code_reply_routes_to_otp_not_job(self, mock_find, mock_complete, mock_trigger):
        """A numeric code still routes to read_otp; it never triggers a job."""
        mock_find.return_value = {"_doc_id": "bhaga_square", "portal": "square"}
        with patch.object(handler, "_find_pending_otp_run") as mock_find_run:
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
            mock_find_run.assert_not_called()
        mock_complete.assert_called_once_with("bhaga_square", "123456")
        mock_trigger.assert_not_called()


# ===========================================================================
# 5. Slash command parsing
# ===========================================================================

class TestSlashCommands:
    @patch.object(handler, "_trigger_cloud_run_job_with_env")
    @patch.object(handler, "_decide_recompute", return_value=False)
    def test_refresh_command(self, mock_decide, mock_trigger, monkeypatch):
        """Ack is generic; real result (with date) arrives via response_url follow-up."""
        follow_ups = _sync_dispatch(monkeypatch)
        with app.test_client() as c:
            resp = _post_command(c, "refresh 2025-05-26")
        assert resp.status_code == 200
        ack = resp.get_json()
        # Ack must NOT contain the date — that belongs to the follow-up
        assert "queued" in ack["text"] or "Refresh" in ack["text"]
        assert "2025-05-26" not in ack["text"]
        # Follow-up has the date and the trigger happened
        assert len(follow_ups) == 1
        assert "2025-05-26" in follow_ups[0]["text"]
        mock_trigger.assert_called_once()
        env_pairs = dict(mock_trigger.call_args[0][1])
        assert env_pairs["REFRESH_DATE"] == "2025-05-26"
        assert env_pairs["BHAGA_OTP_FORCE_REQUEST"] == "1"
        assert env_pairs["BHAGA_IGNORE_HALT"] == "1"

    @patch.object(handler, "_get_latest_run_summary", return_value=":white_check_mark: All good")
    def test_status_command(self, mock_summary, monkeypatch):
        """Status result arrives via response_url follow-up, not in the ack."""
        follow_ups = _sync_dispatch(monkeypatch)
        with app.test_client() as c:
            resp = _post_command(c, "status")
        assert resp.status_code == 200
        ack = resp.get_json()
        assert "All good" not in ack["text"]
        assert "status queued" in ack["text"].lower() or "posting" in ack["text"].lower()
        assert len(follow_ups) == 1
        assert "All good" in follow_ups[0]["text"]

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
# 5b. _parse_refresh_dates — pure parser unit tests
# ===========================================================================

class TestParseRefreshDates:
    """Direct unit tests for the pure parser — no HTTP, no mocks."""

    def test_single_date(self):
        dates, err = handler._parse_refresh_dates("2026-06-14")
        assert err is None
        assert dates == ["2026-06-14"]

    def test_comma_list(self):
        dates, err = handler._parse_refresh_dates("2026-06-14,2026-06-15")
        assert err is None
        assert dates == ["2026-06-14", "2026-06-15"]

    def test_comma_list_with_spaces(self):
        dates, err = handler._parse_refresh_dates("2026-06-14, 2026-06-15")
        assert err is None
        assert dates == ["2026-06-14", "2026-06-15"]

    def test_space_separated_dates(self):
        dates, err = handler._parse_refresh_dates("2026-06-14 2026-06-15 2026-06-16")
        assert err is None
        assert dates == ["2026-06-14", "2026-06-15", "2026-06-16"]

    def test_dotdot_range(self):
        dates, err = handler._parse_refresh_dates("2026-06-14..2026-06-20")
        assert err is None
        assert len(dates) == 7
        assert dates[0] == "2026-06-14"
        assert dates[-1] == "2026-06-20"

    def test_to_range(self):
        dates, err = handler._parse_refresh_dates("2026-06-14 to 2026-06-20")
        assert err is None
        assert len(dates) == 7
        assert dates[0] == "2026-06-14"
        assert dates[-1] == "2026-06-20"

    def test_mixed(self):
        # 1 + 3 + 1 = 5 unique dates
        dates, err = handler._parse_refresh_dates("2026-06-14,2026-06-20..2026-06-22,2026-06-25")
        assert err is None
        assert len(dates) == 5
        assert "2026-06-14" in dates
        assert "2026-06-20" in dates
        assert "2026-06-21" in dates
        assert "2026-06-22" in dates
        assert "2026-06-25" in dates

    def test_dedup(self):
        dates, err = handler._parse_refresh_dates("2026-06-14,2026-06-14")
        assert err is None
        assert dates == ["2026-06-14"]

    def test_sorted_ascending(self):
        dates, err = handler._parse_refresh_dates("2026-06-20,2026-06-14,2026-06-17")
        assert err is None
        assert dates == ["2026-06-14", "2026-06-17", "2026-06-20"]

    def test_reverse_range_error(self):
        dates, err = handler._parse_refresh_dates("2026-06-20..2026-06-14")
        assert dates == []
        assert err is not None
        assert "after" in err

    def test_bad_token(self):
        dates, err = handler._parse_refresh_dates("foo")
        assert dates == []
        assert err is not None

    def test_bad_token_in_range(self):
        dates, err = handler._parse_refresh_dates("2026-06-14..foo")
        assert dates == []
        assert err is not None

    def test_over_cap(self):
        # 2026-01-01 to 2026-07-15 = 196 days → exceeds cap of 31
        dates, err = handler._parse_refresh_dates("2026-01-01..2026-07-15")
        assert dates == []
        assert err is not None
        assert "31" in err or "cap" in err.lower()

    def test_empty_string(self):
        dates, err = handler._parse_refresh_dates("")
        assert dates == []
        assert err is not None


# ===========================================================================
# 5c. _build_refresh_env_overrides + _decide_recompute unit tests
# ===========================================================================

class TestBuildRefreshEnvOverrides:
    def test_recompute_only_has_skip_flags(self):
        env = dict(handler._build_refresh_env_overrides("2026-06-13", recompute_only=True))
        assert env["REFRESH_DATE"] == "2026-06-13"
        assert env["BHAGA_SKIP_SQUARE"] == "1"
        assert env["BHAGA_SKIP_ADP"] == "1"
        assert env["BHAGA_SKIP_KDS"] == "1"
        assert env["BHAGA_IGNORE_HALT"] == "1"
        assert "BHAGA_OTP_FORCE_REQUEST" not in env

    def test_full_scrape_has_otp_flag(self):
        env = dict(handler._build_refresh_env_overrides("2026-06-14", recompute_only=False))
        assert env["REFRESH_DATE"] == "2026-06-14"
        assert env["BHAGA_OTP_FORCE_REQUEST"] == "1"
        assert env["BHAGA_IGNORE_HALT"] == "1"
        assert "BHAGA_SKIP_SQUARE" not in env
        assert "BHAGA_SKIP_ADP" not in env

    def test_decide_recompute_uses_bq_probe(self, monkeypatch):
        monkeypatch.setattr(handler, "_date_is_covered", lambda d, dataset=None: True)
        assert handler._decide_recompute("2026-06-13") is True
        monkeypatch.setattr(handler, "_date_is_covered", lambda d, dataset=None: False)
        assert handler._decide_recompute("2026-06-14") is False

    def test_decide_recompute_fail_open_when_bq_none(self, monkeypatch):
        monkeypatch.setattr(handler, "_bq", None)
        # _date_is_covered returns False when _bq is None → full scrape
        assert handler._decide_recompute("2026-06-14") is False


# ===========================================================================
# 5d. Multi-date refresh — full HTTP command tests
# ===========================================================================

class TestRefreshMultiDate:
    """End-to-end slash-command tests for multi-date refresh parsing and triggering.

    All tests that need to verify triggers or follow-up content use _sync_dispatch()
    to run the background worker synchronously and capture _post_response_url calls.
    Tests that only check parse errors or the generic ack do not need _sync_dispatch.
    """

    def _call_refresh(self, client, text: str):
        return _post_command(client, f"refresh {text}")

    @patch.object(handler, "_trigger_cloud_run_job_with_env")
    @patch.object(handler, "_decide_recompute", return_value=False)
    def test_comma_list_two_dates(self, mock_decide, mock_trigger, monkeypatch):
        follow_ups = _sync_dispatch(monkeypatch)
        with app.test_client() as c:
            resp = self._call_refresh(c, "2026-06-14,2026-06-15")
        assert resp.status_code == 200
        ack = resp.get_json()
        # Ack is generic — dates appear only in the follow-up
        assert "2026-06-14" not in ack["text"]
        assert "2026-06-15" not in ack["text"]
        assert len(follow_ups) == 1
        assert "2026-06-14" in follow_ups[0]["text"]
        assert "2026-06-15" in follow_ups[0]["text"]
        assert mock_trigger.call_count == 2
        triggered_dates = [c[0][0] for c in mock_trigger.call_args_list]
        assert triggered_dates == ["2026-06-14", "2026-06-15"]

    @patch.object(handler, "_trigger_cloud_run_job_with_env")
    @patch.object(handler, "_decide_recompute", return_value=False)
    def test_space_list_three_dates(self, mock_decide, mock_trigger, monkeypatch):
        _sync_dispatch(monkeypatch)
        with app.test_client() as c:
            resp = self._call_refresh(c, "2026-06-14 2026-06-15 2026-06-16")
        assert resp.status_code == 200
        assert mock_trigger.call_count == 3

    @patch.object(handler, "_trigger_cloud_run_job_with_env")
    @patch.object(handler, "_decide_recompute", return_value=False)
    def test_dotdot_range_seven_dates(self, mock_decide, mock_trigger, monkeypatch):
        _sync_dispatch(monkeypatch)
        with app.test_client() as c:
            resp = self._call_refresh(c, "2026-06-14..2026-06-20")
        assert resp.status_code == 200
        assert mock_trigger.call_count == 7
        triggered_dates = [c[0][0] for c in mock_trigger.call_args_list]
        assert triggered_dates[0] == "2026-06-14"
        assert triggered_dates[-1] == "2026-06-20"

    @patch.object(handler, "_trigger_cloud_run_job_with_env")
    @patch.object(handler, "_decide_recompute", return_value=False)
    def test_to_range_seven_dates(self, mock_decide, mock_trigger, monkeypatch):
        _sync_dispatch(monkeypatch)
        with app.test_client() as c:
            resp = self._call_refresh(c, "2026-06-14 to 2026-06-20")
        assert resp.status_code == 200
        assert mock_trigger.call_count == 7

    @patch.object(handler, "_trigger_cloud_run_job_with_env")
    @patch.object(handler, "_decide_recompute", return_value=False)
    def test_mixed_list(self, mock_decide, mock_trigger, monkeypatch):
        _sync_dispatch(monkeypatch)
        with app.test_client() as c:
            resp = self._call_refresh(c, "2026-06-14,2026-06-20..2026-06-22,2026-06-25")
        assert resp.status_code == 200
        assert mock_trigger.call_count == 5

    @patch.object(handler, "_trigger_cloud_run_job_with_env")
    @patch.object(handler, "_decide_recompute", return_value=False)
    def test_dedup(self, mock_decide, mock_trigger, monkeypatch):
        _sync_dispatch(monkeypatch)
        with app.test_client() as c:
            resp = self._call_refresh(c, "2026-06-14,2026-06-14")
        assert resp.status_code == 200
        assert mock_trigger.call_count == 1

    def test_reverse_range_returns_error(self):
        with app.test_client() as c:
            resp = self._call_refresh(c, "2026-06-20..2026-06-14")
        assert resp.status_code == 200
        data = resp.get_json()
        assert ":x:" in data["text"]

    def test_bad_token_returns_error(self):
        with app.test_client() as c:
            resp = _post_command(c, "refresh foo")
        assert resp.status_code == 200
        data = resp.get_json()
        assert ":x:" in data["text"]

    def test_over_cap_returns_error(self):
        with app.test_client() as c:
            resp = self._call_refresh(c, "2026-01-01..2026-07-15")
        assert resp.status_code == 200
        data = resp.get_json()
        assert ":x:" in data["text"]

    @patch.object(handler, "_trigger_cloud_run_job_with_env")
    def test_covered_date_gets_recompute_env(self, mock_trigger, monkeypatch):
        """A BQ-covered date must produce skip flags and no OTP flag."""
        follow_ups = _sync_dispatch(monkeypatch)
        monkeypatch.setattr(handler, "_date_is_covered", lambda d, dataset=None: True)
        with app.test_client() as c:
            resp = self._call_refresh(c, "2026-06-14")
        assert resp.status_code == 200
        env = dict(mock_trigger.call_args[0][1])
        assert env.get("BHAGA_SKIP_SQUARE") == "1"
        assert env.get("BHAGA_SKIP_ADP") == "1"
        assert env.get("BHAGA_SKIP_KDS") == "1"
        assert "BHAGA_OTP_FORCE_REQUEST" not in env
        # Mode label is in the follow-up, not the ack
        assert "recompute" not in resp.get_json()["text"]
        assert len(follow_ups) == 1
        assert "recompute" in follow_ups[0]["text"]

    @patch.object(handler, "_trigger_cloud_run_job_with_env")
    def test_uncovered_date_gets_full_scrape_env(self, mock_trigger, monkeypatch):
        """A BQ-uncovered date must produce the OTP flag and no skip flags."""
        follow_ups = _sync_dispatch(monkeypatch)
        monkeypatch.setattr(handler, "_date_is_covered", lambda d, dataset=None: False)
        with app.test_client() as c:
            resp = self._call_refresh(c, "2026-06-14")
        assert resp.status_code == 200
        env = dict(mock_trigger.call_args[0][1])
        assert env.get("BHAGA_OTP_FORCE_REQUEST") == "1"
        assert "BHAGA_SKIP_SQUARE" not in env
        # Mode label is in the follow-up, not the ack
        assert "full+OTP" not in resp.get_json()["text"]
        assert len(follow_ups) == 1
        assert "full+OTP" in follow_ups[0]["text"]

    @patch.object(handler, "_trigger_cloud_run_job_with_env")
    def test_bq_failure_falls_back_to_full_scrape(self, mock_trigger, monkeypatch):
        """BQ probe failure must fail-open to full scrape, not suppress the trigger."""
        _sync_dispatch(monkeypatch)
        monkeypatch.setattr(handler, "_bq", None)
        with app.test_client() as c:
            resp = self._call_refresh(c, "2026-06-14")
        assert resp.status_code == 200
        mock_trigger.assert_called_once()
        env = dict(mock_trigger.call_args[0][1])
        assert env.get("BHAGA_OTP_FORCE_REQUEST") == "1"

    @patch.object(handler, "_trigger_cloud_run_job_with_env")
    def test_mixed_covered_uncovered_per_date_env(self, mock_trigger, monkeypatch):
        """Each date gets independent coverage probe; covered → recompute, uncovered → full."""
        follow_ups = _sync_dispatch(monkeypatch)
        covered = {"2026-06-14"}
        monkeypatch.setattr(handler, "_date_is_covered", lambda d, dataset=None: d in covered)
        with app.test_client() as c:
            resp = self._call_refresh(c, "2026-06-14,2026-06-15")
        assert resp.status_code == 200
        assert mock_trigger.call_count == 2
        call_map = {c[0][0]: dict(c[0][1]) for c in mock_trigger.call_args_list}
        assert "BHAGA_SKIP_SQUARE" in call_map["2026-06-14"]
        assert "BHAGA_OTP_FORCE_REQUEST" not in call_map["2026-06-14"]
        assert call_map["2026-06-15"].get("BHAGA_OTP_FORCE_REQUEST") == "1"
        assert "BHAGA_SKIP_SQUARE" not in call_map["2026-06-15"]
        # Mode labels are in the follow-up, not the ack
        ack_text = resp.get_json()["text"]
        assert "recompute" not in ack_text
        assert "full+OTP" not in ack_text
        follow_text = follow_ups[0]["text"]
        assert "recompute" in follow_text
        assert "full+OTP" in follow_text

    def test_help_text_shows_range_syntax(self):
        """Help text (unknown command) must document list + range syntax."""
        with app.test_client() as c:
            resp = _post_command(c, "unknown-command-xyz-multi")
        assert resp.status_code == 200
        data = resp.get_json()
        assert ".." in data["text"]
        assert "to" in data["text"]

    # -----------------------------------------------------------------------
    # New tests: async ack contract
    # -----------------------------------------------------------------------

    def test_ack_is_generic_no_dates(self, monkeypatch):
        """The synchronous ack must not contain date strings — only a queued message."""
        monkeypatch.setattr(handler, "_dispatch_async", lambda fn, *a: None)  # don't run worker
        with app.test_client() as c:
            resp = self._call_refresh(c, "2026-06-14,2026-06-15")
        assert resp.status_code == 200
        ack = resp.get_json()
        assert "2026-06-14" not in ack["text"]
        assert "2026-06-15" not in ack["text"]
        assert "queued" in ack["text"] or "Refresh" in ack["text"]

    def test_parse_error_no_worker_dispatched(self, monkeypatch):
        """Parse errors must return inline :x: synchronously; no worker is dispatched."""
        dispatched = []
        monkeypatch.setattr(handler, "_dispatch_async", lambda fn, *a: dispatched.append(fn))
        for bad in ["foo", "2026-06-20..2026-06-14", "2026-01-01..2026-07-15"]:
            dispatched.clear()
            with app.test_client() as c:
                resp = _post_command(c, f"refresh {bad}")
            assert ":x:" in resp.get_json()["text"], f"expected :x: for {bad!r}"
            assert not dispatched, f"worker must not be dispatched for parse error {bad!r}"

    @patch.object(handler, "_trigger_cloud_run_job_with_env")
    @patch.object(handler, "_decide_recompute", return_value=False)
    def test_follow_up_is_in_channel(self, mock_decide, mock_trigger, monkeypatch):
        """Refresh follow-up must use response_type='in_channel' (operational broadcast)."""
        follow_ups = _sync_dispatch(monkeypatch)
        with app.test_client() as c:
            self._call_refresh(c, "2026-06-14")
        assert len(follow_ups) == 1
        assert follow_ups[0]["response_type"] == "in_channel"

    @patch.object(handler, "_trigger_cloud_run_job_with_env")
    @patch.object(handler, "_decide_recompute", return_value=False)
    def test_ack_count_label(self, mock_decide, mock_trigger, monkeypatch):
        """Ack text must include the count of dates queued."""
        monkeypatch.setattr(handler, "_dispatch_async", lambda fn, *a: None)
        with app.test_client() as c:
            resp = self._call_refresh(c, "2026-06-14,2026-06-15,2026-06-16")
        ack = resp.get_json()
        assert "3" in ack["text"]


# ===========================================================================
# 5e. Direct sandbox trigger bypass
# ===========================================================================

class TestSandboxTrigger:
    """Tests for the X-Sandbox-Trigger bypass header path."""

    _TOKEN = "test-sandbox-token-abc123"

    def _post_sandbox(self, client, text: str, token: str = None):
        """POST to /slack/commands with X-Sandbox-Trigger header (no Slack sig)."""
        if token is None:
            token = self._TOKEN
        return client.post(
            "/slack/commands",
            data={
                "text": text,
                "command": "/bhaga-cloud",
                "user_name": "agent",
                "response_url": "https://hooks.slack.com/commands/test/sandbox_response_url",
            },
            headers={"X-Sandbox-Trigger": token},
        )

    @patch.object(handler, "_trigger_cloud_run_job_with_env")
    @patch.object(handler, "_decide_recompute", return_value=False)
    def test_bypass_skips_slack_signature(self, mock_decide, mock_trigger, monkeypatch):
        """Valid token → no Slack HMAC required, request dispatched."""
        _sync_dispatch(monkeypatch)
        monkeypatch.setattr(handler, "_SANDBOX_TRIGGER_TOKEN", self._TOKEN)
        with app.test_client() as c:
            resp = self._post_sandbox(c, "refresh 2026-06-23")
        assert resp.status_code == 200
        # Ack is generic — trigger still fires via worker
        assert "2026-06-23" not in resp.get_json()["text"]
        mock_trigger.assert_called_once()

    @patch.object(handler, "_trigger_cloud_run_job_with_env")
    @patch.object(handler, "_decide_recompute", return_value=False)
    def test_bypass_targets_sandbox_job(self, mock_decide, mock_trigger, monkeypatch):
        """Bypass path must pass the sandbox job resource name to the trigger."""
        _sync_dispatch(monkeypatch)
        monkeypatch.setattr(handler, "_SANDBOX_TRIGGER_TOKEN", self._TOKEN)
        monkeypatch.setattr(
            handler, "_SANDBOX_JOB_RESOURCE",
            "projects/jarvis-bhaga-prod/locations/us-central1/jobs/bhaga-sandbox-refresh",
        )
        with app.test_client() as c:
            resp = self._post_sandbox(c, "refresh 2026-06-23")
        assert resp.status_code == 200
        # job_name is passed as a keyword argument
        call_kwargs = mock_trigger.call_args[1]
        assert call_kwargs.get("job_name") == "projects/jarvis-bhaga-prod/locations/us-central1/jobs/bhaga-sandbox-refresh"

    @patch.object(handler, "_trigger_cloud_run_job_with_env")
    def test_bypass_uses_sandbox_bq_dataset(self, mock_trigger, monkeypatch):
        """Coverage probe receives dataset=bhaga_sandbox so module-global is not mutated."""
        _sync_dispatch(monkeypatch)
        monkeypatch.setattr(handler, "_SANDBOX_TRIGGER_TOKEN", self._TOKEN)
        probe_calls: list = []
        monkeypatch.setattr(handler, "_date_is_covered", lambda d, dataset=None: probe_calls.append(dataset) or False)
        with app.test_client() as c:
            self._post_sandbox(c, "refresh 2026-06-23")
        assert probe_calls, "coverage probe not called"
        assert probe_calls[0] == handler._SANDBOX_BQ_DATASET

    @patch.object(handler, "_trigger_cloud_run_job_with_env")
    @patch.object(handler, "_decide_recompute", return_value=False)
    def test_bypass_ack_has_sandbox_prefix(self, mock_decide, mock_trigger, monkeypatch):
        """Ack text must have the :test_tube: [SANDBOX] prefix; dates are in the follow-up."""
        follow_ups = _sync_dispatch(monkeypatch)
        monkeypatch.setattr(handler, "_SANDBOX_TRIGGER_TOKEN", self._TOKEN)
        with app.test_client() as c:
            resp = self._post_sandbox(c, "refresh 2026-06-23,2026-06-24")
        assert resp.status_code == 200
        ack_text = resp.get_json()["text"]
        assert "[SANDBOX]" in ack_text
        # Dates are in the follow-up, not the ack
        assert "2026-06-23" not in ack_text
        assert "2026-06-24" not in ack_text
        assert len(follow_ups) == 1
        assert "2026-06-23" in follow_ups[0]["text"]
        assert "2026-06-24" in follow_ups[0]["text"]
        assert "[SANDBOX]" in follow_ups[0]["text"]

    def test_wrong_token_returns_403(self, monkeypatch):
        """A non-matching sandbox token must be rejected with 403."""
        monkeypatch.setattr(handler, "_SANDBOX_TRIGGER_TOKEN", self._TOKEN)
        with app.test_client() as c:
            resp = self._post_sandbox(c, "refresh 2026-06-23", token="wrong-token")
        assert resp.status_code == 403

    def test_no_token_env_no_bypass(self, monkeypatch):
        """When SANDBOX_TRIGGER_TOKEN is unset, the bypass header is ignored → falls through to Slack sig check → 403."""
        monkeypatch.setattr(handler, "_SANDBOX_TRIGGER_TOKEN", "")
        # Send with a header that looks like a bypass attempt but env token is unset.
        # With no valid Slack sig, it must fall through to the sig check and get 403.
        with app.test_client() as c:
            resp = c.post(
                "/slack/commands",
                data={"text": "refresh 2026-06-23", "command": "/bhaga-cloud"},
                headers={"X-Sandbox-Trigger": "some-token"},
            )
        # Falls through to Slack sig verification which fails (no real sig) → 403
        assert resp.status_code == 403

    def test_non_refresh_command_via_bypass_rejected(self, monkeypatch):
        """The bypass path must reject non-refresh commands to prevent prod BQ mutation."""
        monkeypatch.setattr(handler, "_SANDBOX_TRIGGER_TOKEN", self._TOKEN)
        for non_refresh in ["config set store_name Test", "training set \"Doe, J\" 2026-06-23", "status"]:
            with app.test_client() as c:
                resp = self._post_sandbox(c, non_refresh)
            assert resp.status_code == 403, f"Expected 403 for '{non_refresh}', got {resp.status_code}"

    @patch.object(handler, "_trigger_cloud_run_job_with_env")
    @patch.object(handler, "_decide_recompute", return_value=False)
    def test_prod_path_unchanged_with_bypass_configured(self, mock_decide, mock_trigger, monkeypatch):
        """Even with SANDBOX_TRIGGER_TOKEN set, a normal Slack-signed request still goes to prod path."""
        _sync_dispatch(monkeypatch)
        monkeypatch.setattr(handler, "_SANDBOX_TRIGGER_TOKEN", self._TOKEN)
        # A Slack-signed POST without the sandbox header must hit the prod path.
        with app.test_client() as c:
            resp = _post_command(c, "refresh 2025-05-26")
        assert resp.status_code == 200
        # Prod path: no [SANDBOX] prefix in the ack
        text = resp.get_json()["text"]
        assert "[SANDBOX]" not in text

    @patch.object(handler, "_trigger_cloud_run_job_with_env")
    @patch.object(handler, "_decide_recompute", return_value=False)
    def test_multi_date_via_bypass(self, mock_decide, mock_trigger, monkeypatch):
        """Full multi-date fan-out works through the bypass path."""
        _sync_dispatch(monkeypatch)
        monkeypatch.setattr(handler, "_SANDBOX_TRIGGER_TOKEN", self._TOKEN)
        with app.test_client() as c:
            resp = self._post_sandbox(c, "refresh 2026-06-23,2026-06-24")
        assert resp.status_code == 200
        assert mock_trigger.call_count == 2
        triggered_dates = [c[0][0] for c in mock_trigger.call_args_list]
        assert "2026-06-23" in triggered_dates
        assert "2026-06-24" in triggered_dates


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


# ===========================================================================
# 8. Sandbox OTP routing (live sandbox run)
# ===========================================================================


class _MultiCollectionDb:
    """Fake Firestore holding several collections of {doc_id: data}."""

    def __init__(self, data: dict[str, dict[str, dict]]):
        self._data = data

    def collection(self, name):
        docs = self._data.setdefault(name, {})
        col = MagicMock()

        def _stream():
            for doc_id, payload in docs.items():
                snap = MagicMock()
                snap.id = doc_id
                snap.to_dict = lambda payload=payload: dict(payload)
                yield snap

        def _document(doc_id):
            ref = MagicMock()

            def _set(data, merge=False):
                if merge and doc_id in docs:
                    docs[doc_id].update(data)
                else:
                    docs[doc_id] = dict(data)

            ref.set = _set
            return ref

        col.stream = _stream
        col.document = _document
        return col


class TestSandboxOtpRouting:
    def _pending(self, *, env="prod", target_job="", requested_at="2026-05-31T21:00:00-05:00"):
        return {
            "pending_otp": {
                "agent": "bhaga",
                "ready_received": False,
                "requested_at": requested_at,
                "portals": ["Square"],
                "env": env,
                "target_job": target_job,
            }
        }

    def test_no_sandbox_collection_scans_only_prod(self, monkeypatch):
        monkeypatch.setattr(handler, "SANDBOX_RUNS_COLLECTION", "")
        db = _MultiCollectionDb({"runs": {"2026-05-31": self._pending()}})
        with patch.object(handler, "db", db):
            found = handler._find_pending_otp_run("bhaga")
        assert found["collection"] == "runs"
        assert found["date"] == "2026-05-31"

    def test_sandbox_takes_precedence_over_prod(self, monkeypatch):
        monkeypatch.setattr(handler, "SANDBOX_RUNS_COLLECTION", "sandbox_runs")
        sandbox_job = "projects/p/locations/us-central1/jobs/bhaga-sandbox-refresh"
        db = _MultiCollectionDb({
            "runs": {"2026-05-31": self._pending()},
            "sandbox_runs": {"2026-05-31": self._pending(env="sandbox", target_job=sandbox_job)},
        })
        with patch.object(handler, "db", db):
            found = handler._find_pending_otp_run("bhaga")
        # Sandbox wins even though a prod run is also pending.
        assert found["collection"] == "sandbox_runs"
        assert found["pending_otp"]["target_job"] == sandbox_job

    def test_ready_reply_resumes_sandbox_job(self, monkeypatch):
        monkeypatch.setattr(handler, "SANDBOX_RUNS_COLLECTION", "sandbox_runs")
        sandbox_job = "projects/p/locations/us-central1/jobs/bhaga-sandbox-refresh"
        db = _MultiCollectionDb({
            "runs": {"2026-05-31": self._pending()},
            "sandbox_runs": {"2026-05-31": self._pending(env="sandbox", target_job=sandbox_job)},
        })
        with patch.object(handler, "db", db), \
                patch.object(handler, "_trigger_cloud_run_job") as mock_trigger:
            assert handler._handle_ready_reply("bhaga") is True
        # The sandbox job is triggered with its explicit resource name, NOT prod.
        mock_trigger.assert_called_once_with("2026-05-31", job_name=sandbox_job)
        # And the sandbox checkpoint (not prod) was marked ready.
        assert db._data["sandbox_runs"]["2026-05-31"]["pending_otp"]["ready_received"] is True
        assert db._data["runs"]["2026-05-31"]["pending_otp"]["ready_received"] is False

    def test_prod_ready_reply_uses_default_job_single_arg(self, monkeypatch):
        # No sandbox configured, prod pending with no target_job → legacy single-arg call.
        monkeypatch.setattr(handler, "SANDBOX_RUNS_COLLECTION", "")
        db = _MultiCollectionDb({"runs": {"2026-05-31": self._pending()}})
        with patch.object(handler, "db", db), \
                patch.object(handler, "_trigger_cloud_run_job") as mock_trigger:
            assert handler._handle_ready_reply("bhaga") is True
        mock_trigger.assert_called_once_with("2026-05-31")


# ---------------------------------------------------------------------------
# Slash command: config get / config set
# ---------------------------------------------------------------------------


class TestConfigCommands:
    """Verify /bhaga-cloud config get/set parse and call BQ correctly.

    All tests use _sync_dispatch so the worker runs synchronously and
    _post_response_url calls are captured in follow_ups.
    """

    def _make_bq_row(self, value: str, updated_by: str = "alice", updated_at: str = "2026-06-01 00:00:00") -> object:
        """Return a minimal fake BQ row dict."""
        class _Row(dict):
            pass
        row = _Row(value=value, updated_by=updated_by, updated_at=updated_at)
        return row

    def test_config_get_returns_value(self, monkeypatch):
        """config get returns the stored value via response_url follow-up."""
        follow_ups = _sync_dispatch(monkeypatch)
        fake_bq = MagicMock()
        fake_bq.query.return_value.result.return_value = [
            self._make_bq_row("12.5", updated_by="alice"),
        ]
        monkeypatch.setattr(handler, "_bq", fake_bq)
        with app.test_client() as client:
            resp = _post_command(client, "config get saturation_orders_per_labor_hour",
                                 user_name="alice")
        assert resp.status_code == 200
        # Ack is generic; result is in the follow-up
        assert "12.5" not in resp.get_json()["text"]
        assert len(follow_ups) == 1
        assert "12.5" in follow_ups[0]["text"]
        assert "saturation_orders_per_labor_hour" in follow_ups[0]["text"]

    def test_config_get_not_found(self, monkeypatch):
        """config get with missing key returns informational message via follow-up."""
        follow_ups = _sync_dispatch(monkeypatch)
        fake_bq = MagicMock()
        fake_bq.query.return_value.result.return_value = []
        monkeypatch.setattr(handler, "_bq", fake_bq)
        with app.test_client() as client:
            resp = _post_command(client, "config get nonexistent_key")
        assert resp.status_code == 200
        assert len(follow_ups) == 1
        assert "not set" in follow_ups[0]["text"]

    def test_config_set_upserts_and_confirms(self, monkeypatch):
        """config set calls BQ MERGE and confirms the new value via follow-up."""
        follow_ups = _sync_dispatch(monkeypatch)
        fake_bq = MagicMock()
        fake_bq.query.return_value.result.return_value = []
        monkeypatch.setattr(handler, "_bq", fake_bq)
        with app.test_client() as client:
            resp = _post_command(client, "config set saturation_orders_per_labor_hour 11.5",
                                 user_name="bob")
        assert resp.status_code == 200
        assert len(follow_ups) == 1
        assert "11.5" in follow_ups[0]["text"]
        assert "saturation_orders_per_labor_hour" in follow_ups[0]["text"]
        assert fake_bq.query.called

    def test_config_unavailable_without_bq(self, monkeypatch):
        """When BQ is unavailable, config commands post a warning via follow-up."""
        follow_ups = _sync_dispatch(monkeypatch)
        monkeypatch.setattr(handler, "_bq", None)
        with app.test_client() as client:
            resp = _post_command(client, "config get some_key")
        assert resp.status_code == 200
        assert len(follow_ups) == 1
        assert "unavailable" in follow_ups[0]["text"].lower() or "not available" in follow_ups[0]["text"].lower()

    def test_help_text_lists_config_commands(self):
        """The help text (unknown command) lists config get/set."""
        with app.test_client() as client:
            resp = _post_command(client, "unknown-command")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert "config get" in body["text"]
        assert "config set" in body["text"]


# ---------------------------------------------------------------------------
# Slash commands: training set/rm, alias set, exclude set
# ---------------------------------------------------------------------------


class TestTrainingCommands:
    """Verify /bhaga-cloud training set|rm parse and call BQ correctly."""

    def test_training_set_merges_bq(self, monkeypatch):
        follow_ups = _sync_dispatch(monkeypatch)
        fake_bq = MagicMock()
        fake_bq.query.return_value.result.return_value = []
        monkeypatch.setattr(handler, "_bq", fake_bq)
        with app.test_client() as client:
            resp = _post_command(client, 'training set "Flores, Juan" 2026-06-01 first shift',
                                 user_name="adi")
        assert resp.status_code == 200
        assert len(follow_ups) == 1
        assert "Flores, Juan" in follow_ups[0]["text"]
        assert "2026-06-01" in follow_ups[0]["text"]
        assert fake_bq.query.called
        call_sql = fake_bq.query.call_args[0][0]
        assert "MERGE" in call_sql
        assert "training_shifts" in call_sql

    def test_training_set_with_note(self, monkeypatch):
        follow_ups = _sync_dispatch(monkeypatch)
        fake_bq = MagicMock()
        fake_bq.query.return_value.result.return_value = []
        monkeypatch.setattr(handler, "_bq", fake_bq)
        with app.test_client() as client:
            resp = _post_command(client, 'training set "Smith, Alice" 2026-06-02 orientation',
                                 user_name="adi")
        assert resp.status_code == 200
        assert len(follow_ups) == 1
        assert "Smith, Alice" in follow_ups[0]["text"]
        assert "orientation" in follow_ups[0]["text"]

    def test_training_set_without_note(self, monkeypatch):
        follow_ups = _sync_dispatch(monkeypatch)
        fake_bq = MagicMock()
        fake_bq.query.return_value.result.return_value = []
        monkeypatch.setattr(handler, "_bq", fake_bq)
        with app.test_client() as client:
            resp = _post_command(client, 'training set "Smith, Alice" 2026-06-02',
                                 user_name="adi")
        assert resp.status_code == 200
        assert len(follow_ups) == 1
        assert "Smith, Alice" in follow_ups[0]["text"]

    def test_training_rm_deletes_from_bq(self, monkeypatch):
        follow_ups = _sync_dispatch(monkeypatch)
        fake_bq = MagicMock()
        fake_bq.query.return_value.result.return_value = []
        monkeypatch.setattr(handler, "_bq", fake_bq)
        with app.test_client() as client:
            resp = _post_command(client, 'training rm "Flores, Juan" 2026-06-01',
                                 user_name="adi")
        assert resp.status_code == 200
        assert len(follow_ups) == 1
        assert "Flores, Juan" in follow_ups[0]["text"]
        call_sql = fake_bq.query.call_args[0][0]
        assert "DELETE" in call_sql
        assert "training_shifts" in call_sql

    def test_training_unavailable_without_bq(self, monkeypatch):
        follow_ups = _sync_dispatch(monkeypatch)
        monkeypatch.setattr(handler, "_bq", None)
        with app.test_client() as client:
            resp = _post_command(client, 'training set "A, B" 2026-06-01')
        assert resp.status_code == 200
        assert len(follow_ups) == 1
        assert "not available" in follow_ups[0]["text"].lower() or "unavailable" in follow_ups[0]["text"].lower()


class TestAliasCommands:
    """Verify /bhaga-cloud alias set parse and BQ call."""

    def test_alias_set_merges_bq(self, monkeypatch):
        follow_ups = _sync_dispatch(monkeypatch)
        fake_bq = MagicMock()
        fake_bq.query.return_value.result.return_value = []
        monkeypatch.setattr(handler, "_bq", fake_bq)
        with app.test_client() as client:
            resp = _post_command(client, 'alias set "Juan Flores" "Flores, Juan"',
                                 user_name="adi")
        assert resp.status_code == 200
        assert len(follow_ups) == 1
        assert "Juan Flores" in follow_ups[0]["text"]
        assert "Flores, Juan" in follow_ups[0]["text"]
        call_sql = fake_bq.query.call_args[0][0]
        assert "MERGE" in call_sql
        assert "employee_aliases" in call_sql

    def test_alias_set_unavailable_without_bq(self, monkeypatch):
        follow_ups = _sync_dispatch(monkeypatch)
        monkeypatch.setattr(handler, "_bq", None)
        with app.test_client() as client:
            resp = _post_command(client, 'alias set rawname "Canonical, Name"')
        assert resp.status_code == 200
        assert len(follow_ups) == 1
        assert "not available" in follow_ups[0]["text"].lower() or "unavailable" in follow_ups[0]["text"].lower()


class TestExcludeCommands:
    """Verify /bhaga-cloud exclude set parse and store_config call."""

    def test_exclude_set_with_through_date_delegates_to_config_set(self, monkeypatch):
        follow_ups = _sync_dispatch(monkeypatch)
        fake_bq = MagicMock()
        fake_bq.query.return_value.result.return_value = []
        monkeypatch.setattr(handler, "_bq", fake_bq)
        with app.test_client() as client:
            resp = _post_command(client, 'exclude set "Flores, Juan" 2026-05-31',
                                 user_name="adi")
        assert resp.status_code == 200
        assert len(follow_ups) == 1
        assert "training_excluded:Flores, Juan" in follow_ups[0]["text"] or "2026-05-31" in follow_ups[0]["text"]

    def test_exclude_set_without_date_appends_permanent(self, monkeypatch):
        follow_ups = _sync_dispatch(monkeypatch)
        fake_bq = MagicMock()
        fake_bq.query.return_value.result.side_effect = [
            [{"value": "Krause, Lindsay"}],  # first query reads existing permanent list
            [],  # second query MERGE
        ]
        monkeypatch.setattr(handler, "_bq", fake_bq)
        with app.test_client() as client:
            resp = _post_command(client, 'exclude set "New, Person"',
                                 user_name="adi")
        assert resp.status_code == 200

    def test_help_text_lists_new_commands(self):
        with app.test_client() as client:
            resp = _post_command(client, "unknown-command-xyz")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert "training set" in body["text"]
        assert "training rm" in body["text"]
        assert "alias set" in body["text"]
        assert "exclude set" in body["text"]


# ===========================================================================
# Slack-retry dedup and already-running guard
# ===========================================================================

class TestSlackRetryDedup:
    """Slack re-delivers events with X-Slack-Retry-Num > 0.
    A retried READY reply must NOT trigger a second Cloud Run execution.
    """

    def _post_event_with_retry_header(self, client, payload, retry_num: str):
        body = json.dumps(payload).encode("utf-8")
        timestamp = str(int(time.time()))
        signature = _make_signature(body, timestamp)
        return client.post(
            "/slack/events",
            data=body,
            content_type="application/json",
            headers={
                "X-Slack-Request-Timestamp": timestamp,
                "X-Slack-Signature": signature,
                "X-Slack-Retry-Num": retry_num,
                "X-Slack-Retry-Reason": "http_error",
            },
        )

    def test_retry_num_1_is_discarded(self):
        """A delivery with X-Slack-Retry-Num=1 is short-circuited with 200."""
        payload = {
            "type": "event_callback",
            "event_id": "Ev_retry_test",
            "event": {
                "type": "message",
                "channel": "D_BHAGA",
                "user": "U_OP",
                "text": "ready",
            },
        }
        with app.test_client() as c:
            with patch.object(handler, "_handle_event") as mock_handle:
                resp = self._post_event_with_retry_header(c, payload, retry_num="1")
        assert resp.status_code == 200
        mock_handle.assert_not_called()

    def test_retry_num_0_is_processed(self):
        """A delivery with X-Slack-Retry-Num=0 (first attempt) is processed normally."""
        payload = {
            "type": "event_callback",
            "event_id": "Ev_first_delivery",
            "event": {
                "type": "message",
                "channel": "D_BHAGA",
                "user": "U_OP",
                "text": "ready",
            },
        }
        with app.test_client() as c:
            with patch.object(handler, "_handle_event") as mock_handle, \
                 patch.object(handler, "_check_and_store_event_id", return_value=False):
                resp = self._post_event_with_retry_header(c, payload, retry_num="0")
        assert resp.status_code == 200
        mock_handle.assert_called_once()

    def test_no_retry_header_is_processed(self):
        """Events without a retry header (normal case) are processed normally."""
        payload = {
            "type": "event_callback",
            "event_id": "Ev_no_retry",
            "event": {
                "type": "message",
                "channel": "D_BHAGA",
                "user": "U_OP",
                "text": "ready",
            },
        }
        with app.test_client() as c:
            with patch.object(handler, "_handle_event") as mock_handle, \
                 patch.object(handler, "_check_and_store_event_id", return_value=False):
                resp = _post_event(c, payload)
        assert resp.status_code == 200
        mock_handle.assert_called_once()

    def test_duplicate_event_id_is_discarded(self):
        """If _check_and_store_event_id returns True (seen before), _handle_event is skipped."""
        payload = {
            "type": "event_callback",
            "event_id": "Ev_dup",
            "event": {
                "type": "message",
                "channel": "D_BHAGA",
                "user": "U_OP",
                "text": "ready",
            },
        }
        with app.test_client() as c:
            with patch.object(handler, "_handle_event") as mock_handle, \
                 patch.object(handler, "_check_and_store_event_id", return_value=True):
                resp = _post_event(c, payload)
        assert resp.status_code == 200
        mock_handle.assert_not_called()

    def test_is_slack_retry_helper(self):
        assert handler._is_slack_retry({"X-Slack-Retry-Num": "1"}) is True
        assert handler._is_slack_retry({"X-Slack-Retry-Num": "2"}) is True
        assert handler._is_slack_retry({"X-Slack-Retry-Num": "0"}) is False
        assert handler._is_slack_retry({}) is False
        assert handler._is_slack_retry({"X-Slack-Retry-Num": "bad"}) is False


class TestAlreadyRunningGuard:
    """_trigger_cloud_run_job must skip if a non-terminal execution already exists."""

    def _mock_run_v2(self):
        """Return a mock run_v2 module that can be injected into sys.modules."""
        import sys
        mock_rv2 = MagicMock()
        mock_rv2.JobsClient.return_value = MagicMock()
        mock_rv2.ExecutionsClient.return_value = MagicMock()
        return mock_rv2

    def test_trigger_skips_when_already_running(self):
        """When _is_already_running returns True, run_job is not called."""
        import sys
        mock_rv2 = self._mock_run_v2()
        with patch.object(handler, "_is_already_running", return_value=True) as mock_check, \
             patch.dict(sys.modules, {"google.cloud.run_v2": mock_rv2}):
            handler._trigger_cloud_run_job("2026-06-09", job_name="projects/p/jobs/bhaga")
        mock_rv2.JobsClient.return_value.run_job.assert_not_called()
        mock_check.assert_called_once_with("projects/p/jobs/bhaga", "2026-06-09")

    def test_trigger_fires_when_not_running(self):
        """When _is_already_running returns False, run_job is called."""
        import sys
        mock_rv2 = self._mock_run_v2()
        with patch.object(handler, "_is_already_running", return_value=False), \
             patch.dict(sys.modules, {"google.cloud.run_v2": mock_rv2}):
            handler._trigger_cloud_run_job("2026-06-09", job_name="projects/p/jobs/bhaga")
        mock_rv2.JobsClient.return_value.run_job.assert_called_once()

    def test_trigger_fires_when_already_running_check_errors(self):
        """Fail-open: _is_already_running returns False on any API error."""
        import sys
        mock_rv2 = self._mock_run_v2()
        mock_rv2.ExecutionsClient.return_value.list_executions.side_effect = Exception("api error")
        with patch.dict(sys.modules, {"google.cloud.run_v2": mock_rv2}):
            result = handler._is_already_running("projects/p/jobs/bhaga", "2026-06-09")
        assert result is False  # fail-open: allow trigger

    def test_no_job_name_skips_trigger(self):
        """Without CLOUD_RUN_JOB_NAME env var set, no trigger fires."""
        import sys
        mock_rv2 = self._mock_run_v2()
        old = os.environ.pop("CLOUD_RUN_JOB_NAME", None)
        try:
            with patch.object(handler, "_is_already_running") as mock_check, \
                 patch.dict(sys.modules, {"google.cloud.run_v2": mock_rv2}):
                handler._trigger_cloud_run_job("2026-06-09")
            mock_check.assert_not_called()
            mock_rv2.JobsClient.return_value.run_job.assert_not_called()
        finally:
            if old is not None:
                os.environ["CLOUD_RUN_JOB_NAME"] = old

    def test_force_otp_request_adds_env_flag(self):
        import sys
        mock_rv2 = self._mock_run_v2()
        with patch.object(handler, "_is_already_running", return_value=False), \
             patch.dict(sys.modules, {"google.cloud.run_v2": mock_rv2}):
            handler._trigger_cloud_run_job(
                "2026-06-14", job_name="projects/p/jobs/bhaga", force_otp_request=True,
            )
        names = {
            c.kwargs.get("name"): c.kwargs.get("value")
            for c in mock_rv2.EnvVar.call_args_list
        }
        assert names.get("REFRESH_DATE") == "2026-06-14"
        assert names.get("BHAGA_OTP_FORCE_REQUEST") == "1"

    def test_no_force_omits_env_flag(self):
        import sys
        mock_rv2 = self._mock_run_v2()
        with patch.object(handler, "_is_already_running", return_value=False), \
             patch.dict(sys.modules, {"google.cloud.run_v2": mock_rv2}):
            handler._trigger_cloud_run_job("2026-06-14", job_name="projects/p/jobs/bhaga")
        names = {
            c.kwargs.get("name"): c.kwargs.get("value")
            for c in mock_rv2.EnvVar.call_args_list
        }
        assert "BHAGA_OTP_FORCE_REQUEST" not in names
