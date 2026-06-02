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
