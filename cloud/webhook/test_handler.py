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
            mock_trigger.assert_called_once_with("2025-05-26", force_otp_request=True)

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


# ---------------------------------------------------------------------------
# Slash command: config get / config set
# ---------------------------------------------------------------------------


class TestConfigCommands:
    """Verify /bhaga-cloud config get/set parse and call BQ correctly."""

    def _make_bq_row(self, value: str, updated_by: str = "alice", updated_at: str = "2026-06-01 00:00:00") -> object:
        """Return a minimal fake BQ row dict."""
        class _Row(dict):
            pass
        row = _Row(value=value, updated_by=updated_by, updated_at=updated_at)
        return row

    def test_config_get_returns_value(self, monkeypatch):
        """config get returns the stored value."""
        fake_bq = MagicMock()
        fake_bq.query.return_value.result.return_value = [
            self._make_bq_row("12.5", updated_by="alice"),
        ]
        monkeypatch.setattr(handler, "_bq", fake_bq)
        with app.test_client() as client:
            resp = _post_command(client, "config get saturation_orders_per_labor_hour",
                                 user_name="alice")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert "12.5" in body["text"]
        assert "saturation_orders_per_labor_hour" in body["text"]

    def test_config_get_not_found(self, monkeypatch):
        """config get with missing key returns informational message."""
        fake_bq = MagicMock()
        fake_bq.query.return_value.result.return_value = []
        monkeypatch.setattr(handler, "_bq", fake_bq)
        with app.test_client() as client:
            resp = _post_command(client, "config get nonexistent_key")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert "not set" in body["text"]

    def test_config_set_upserts_and_confirms(self, monkeypatch):
        """config set calls BQ MERGE and confirms the new value."""
        fake_bq = MagicMock()
        fake_bq.query.return_value.result.return_value = []
        monkeypatch.setattr(handler, "_bq", fake_bq)
        with app.test_client() as client:
            resp = _post_command(client, "config set saturation_orders_per_labor_hour 11.5",
                                 user_name="bob")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert "11.5" in body["text"]
        assert "saturation_orders_per_labor_hour" in body["text"]
        assert fake_bq.query.called

    def test_config_unavailable_without_bq(self, monkeypatch):
        """When BQ is unavailable, config commands return a warning."""
        monkeypatch.setattr(handler, "_bq", None)
        with app.test_client() as client:
            resp = _post_command(client, "config get some_key")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert "unavailable" in body["text"].lower() or "not available" in body["text"].lower()

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
        fake_bq = MagicMock()
        fake_bq.query.return_value.result.return_value = []
        monkeypatch.setattr(handler, "_bq", fake_bq)
        with app.test_client() as client:
            resp = _post_command(client, 'training set "Flores, Juan" 2026-06-01 first shift',
                                 user_name="adi")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert "Flores, Juan" in body["text"]
        assert "2026-06-01" in body["text"]
        assert fake_bq.query.called
        call_sql = fake_bq.query.call_args[0][0]
        assert "MERGE" in call_sql
        assert "training_shifts" in call_sql

    def test_training_set_with_note(self, monkeypatch):
        fake_bq = MagicMock()
        fake_bq.query.return_value.result.return_value = []
        monkeypatch.setattr(handler, "_bq", fake_bq)
        with app.test_client() as client:
            resp = _post_command(client, 'training set "Smith, Alice" 2026-06-02 orientation',
                                 user_name="adi")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert "Smith, Alice" in body["text"]
        assert "orientation" in body["text"]

    def test_training_set_without_note(self, monkeypatch):
        fake_bq = MagicMock()
        fake_bq.query.return_value.result.return_value = []
        monkeypatch.setattr(handler, "_bq", fake_bq)
        with app.test_client() as client:
            resp = _post_command(client, 'training set "Smith, Alice" 2026-06-02',
                                 user_name="adi")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert "Smith, Alice" in body["text"]

    def test_training_rm_deletes_from_bq(self, monkeypatch):
        fake_bq = MagicMock()
        fake_bq.query.return_value.result.return_value = []
        monkeypatch.setattr(handler, "_bq", fake_bq)
        with app.test_client() as client:
            resp = _post_command(client, 'training rm "Flores, Juan" 2026-06-01',
                                 user_name="adi")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert "Flores, Juan" in body["text"]
        call_sql = fake_bq.query.call_args[0][0]
        assert "DELETE" in call_sql
        assert "training_shifts" in call_sql

    def test_training_unavailable_without_bq(self, monkeypatch):
        monkeypatch.setattr(handler, "_bq", None)
        with app.test_client() as client:
            resp = _post_command(client, 'training set "A, B" 2026-06-01')
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert "not available" in body["text"].lower() or "unavailable" in body["text"].lower()


class TestAliasCommands:
    """Verify /bhaga-cloud alias set parse and BQ call."""

    def test_alias_set_merges_bq(self, monkeypatch):
        fake_bq = MagicMock()
        fake_bq.query.return_value.result.return_value = []
        monkeypatch.setattr(handler, "_bq", fake_bq)
        with app.test_client() as client:
            resp = _post_command(client, 'alias set "Juan Flores" "Flores, Juan"',
                                 user_name="adi")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert "Juan Flores" in body["text"]
        assert "Flores, Juan" in body["text"]
        call_sql = fake_bq.query.call_args[0][0]
        assert "MERGE" in call_sql
        assert "employee_aliases" in call_sql

    def test_alias_set_unavailable_without_bq(self, monkeypatch):
        monkeypatch.setattr(handler, "_bq", None)
        with app.test_client() as client:
            resp = _post_command(client, 'alias set rawname "Canonical, Name"')
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert "not available" in body["text"].lower() or "unavailable" in body["text"].lower()


class TestExcludeCommands:
    """Verify /bhaga-cloud exclude set parse and store_config call."""

    def test_exclude_set_with_through_date_delegates_to_config_set(self, monkeypatch):
        fake_bq = MagicMock()
        fake_bq.query.return_value.result.return_value = []
        monkeypatch.setattr(handler, "_bq", fake_bq)
        with app.test_client() as client:
            resp = _post_command(client, 'exclude set "Flores, Juan" 2026-05-31',
                                 user_name="adi")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert "training_excluded:Flores, Juan" in body["text"] or "2026-05-31" in body["text"]

    def test_exclude_set_without_date_appends_permanent(self, monkeypatch):
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
