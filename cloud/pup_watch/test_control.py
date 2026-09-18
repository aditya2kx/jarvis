import base64
import urllib.parse
from email.utils import formatdate

import pytest

from cloud.pup_watch import control, notify, persist, sessions, worker
from cloud.pup_watch.config import Settings

NOW = 1_700_000_000.0
ME = "owner@example.com"
PARTNER = "partner@example.com"
GOOD_AUTH = "mx.google.com; spf=pass smtp.mailfrom=owner@example.com; dkim=pass header.i=@example.com"


@pytest.fixture(autouse=True)
def _recipients(monkeypatch):
    monkeypatch.setenv("PUPWATCH_NOTIFY_TO", f"{ME},{PARTNER}")


@pytest.fixture
def store(monkeypatch):
    """In-memory stand-in for the Firestore docs."""
    docs = {"config": {}, "session": {}, "state": {}}
    monkeypatch.setattr(persist, "_load", lambda doc: dict(docs.get(doc, {})))
    monkeypatch.setattr(persist, "_save", lambda doc, payload: docs[doc].update(payload))
    return docs


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _message(body, *, sender=ME, subject="Re: Pup is out in the S/M YARD",
             auth=GOOD_AUTH, labels=("INBOX", "UNREAD"), ts=NOW, ours=False):
    headers = [
        {"name": "From", "value": f"Someone <{sender}>"},
        {"name": "Subject", "value": subject},
        {"name": "Authentication-Results", "value": auth},
        {"name": "Date", "value": formatdate(ts)},
    ]
    if ours:
        headers.append({"name": notify.MARKER_HEADER, "value": "1"})
    return {
        "id": "m1",
        "labelIds": list(labels),
        "internalDate": str(int(ts * 1000)),
        "payload": {
            "mimeType": "text/plain",
            "body": {"data": _b64(body)},
            "headers": headers,
        },
    }


class FakeGmail:
    """Records modify calls so consumption is observable."""

    LABEL_ID = "Label_99"

    def __init__(self, messages):
        self.messages = messages
        self.handled = []      # ids we labelled as handled
        self.marked_read = []  # ids whose UNREAD we cleared
        self.queries = []
        self.labels_created = 0

    def api(self, access, path, *, method="GET", payload=None):
        if path == "labels" and method == "GET":
            return {"labels": [{"id": self.LABEL_ID, "name": control.HANDLED_LABEL}]}
        if path == "labels" and method == "POST":
            self.labels_created += 1
            return {"id": self.LABEL_ID}
        if path.startswith("messages?"):
            self.queries.append(path)
            return {"messages": [{"id": m["id"]} for m in self._search(path)]}
        if "/modify" in path:
            mid = path.split("/")[1]
            msg = next(m for m in self.messages if m["id"] == mid)
            if self.LABEL_ID in (payload or {}).get("addLabelIds", []):
                self.handled.append(mid)
                msg["labelIds"] = list(msg.get("labelIds") or ()) + [self.LABEL_ID]
            if "UNREAD" in (payload or {}).get("removeLabelIds", []):
                self.marked_read.append(mid)
                msg["labelIds"] = [l for l in (msg.get("labelIds") or ()) if l != "UNREAD"]
            return {}
        mid = path.split("/")[1].split("?")[0]
        return next(m for m in self.messages if m["id"] == mid)

    def _search(self, path):
        """Honour the query's label operators.

        A fake that returns everything regardless of `q` is how the is:unread
        bug reached production: the real Gmail filtered the operator's replies
        out and the fake did not. Anything asserting on discovery has to model
        the filter it depends on.
        """
        q = urllib.parse.unquote_plus(urllib.parse.parse_qs(
            path.split("?", 1)[1]).get("q", [""])[0])
        out = []
        for m in self.messages:
            labels = set(m.get("labelIds") or ())
            if "is:unread" in q and "UNREAD" not in labels:
                continue
            if f"-label:{control.HANDLED_LABEL}" in q and self.LABEL_ID in labels:
                continue
            out.append(m)
        return out


@pytest.fixture(autouse=True)
def _clear_label_cache():
    control._label_id_cache.clear()
    yield
    control._label_id_cache.clear()


@pytest.fixture
def gmail(monkeypatch):
    def install(messages):
        fake = FakeGmail(messages)
        monkeypatch.setattr(control, "_api", fake.api)
        monkeypatch.setattr(notify, "access_token", lambda: "tok")
        monkeypatch.setattr(control, "_confirm", lambda access, cmd, summary: None)
        return fake
    return install


# --- command parsing -------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("start", ("start", None)),
    ("Start", ("start", None)),
    ("STOP", ("stop", None)),
    ("status", ("status", None)),
    ("start 4h", ("start", 4.0)),
    ("start 2 hours", ("start", 2.0)),
    ("start 1.5hr", ("start", 1.5)),
    ("pup stop", ("stop", None)),
    ("pup-watch start", None),  # only "pup " / "pup-" prefix, not the full name
])
def test_parse_command_variants(text, expected):
    assert control.parse_command(text) == expected


@pytest.mark.parametrize("text", [
    "stopped raining, he loved it",
    "please stop monitoring",
    "I want to start thinking about dinner",
    "",
    "thanks!",
])
def test_parse_command_ignores_prose_that_merely_contains_a_keyword(text):
    """A bare keyword anywhere in a chatty reply must not control the system."""
    assert control.parse_command(text) is None


def test_first_unquoted_line_skips_gmails_quoted_reply():
    body = "\n".join([
        "", "stop", "",
        "On Wed, Sep 16, 2026 at 10:15 AM Someone wrote:",
        "> Spotted at: 10:15:25 AM CDT",
        "> start",
    ])
    assert control._first_unquoted_line(body) == "stop"


def test_quoted_command_alone_is_not_a_command():
    """The quoted original must not re-trigger what it originally reported."""
    body = "On Wed wrote:\n> start 9h\n"
    assert control.parse_command(control._first_unquoted_line(body)) is None


def test_plain_text_walks_multipart():
    payload = {"mimeType": "multipart/alternative", "parts": [
        {"mimeType": "text/html", "body": {"data": _b64("<b>stop</b>")}},
        {"mimeType": "text/plain", "body": {"data": _b64("stop")}},
    ]}
    assert control.plain_text(payload) == "stop"


# --- authentication gates --------------------------------------------------

@pytest.mark.parametrize("header,ok", [
    (GOOD_AUTH, True),
    ("spf=pass; dkim=fail", False),
    ("spf=softfail; dkim=pass", False),
    ("", False),
    ("spf=pass", False),
])
def test_email_authenticated_requires_both_spf_and_dkim(header, ok):
    assert control.email_authenticated(header) is ok


def test_sender_address_extracts_and_lowercases():
    assert control.sender_address("Adi <Owner@Example.COM>") == "owner@example.com"
    assert control.sender_address("owner@example.com") == "owner@example.com"


def test_stranger_cannot_control_monitoring(store, gmail):
    fake = gmail([_message("start", sender="attacker@evil.example.com")])
    assert control.find_commands("tok", settings=Settings(), now=NOW) == []


def test_spoofed_sender_without_dkim_is_refused_and_consumed(store, gmail):
    """Anyone can forge From; only Gmail can write Authentication-Results."""
    fake = gmail([_message("stop", auth="spf=fail; dkim=fail")])
    assert control.find_commands("tok", settings=Settings(), now=NOW) == []
    # Consumed, so it cannot be retried a minute later.
    assert fake.handled == ["m1"]
    assert fake.marked_read == ["m1"]


def test_auth_gate_can_be_relaxed_for_tests_only(store, gmail):
    gmail([_message("stop", auth="")])
    found = control.find_commands(
        "tok", settings=Settings(control_require_email_auth=False), now=NOW)
    assert [c.action for c in found] == ["stop"]


def test_our_own_sighting_email_is_ignored(store, gmail):
    """Our mail lands in INBOX too — it must never look like a command."""
    gmail([_message("start", labels=("INBOX", "UNREAD", "SENT"), ours=True)])
    assert control.find_commands("tok", settings=Settings(), now=NOW) == []


def test_operator_reply_from_the_sending_mailbox_still_counts(store, gmail):
    """Regression: the operator sends FROM the notification mailbox, so his own
    reply is labelled SENT+INBOX. Keying "ignore our own mail" on the SENT label
    would have silently dropped every command he issued."""
    gmail([_message("stop", labels=("INBOX", "UNREAD", "SENT"), ours=False)])
    found = control.find_commands("tok", settings=Settings(), now=NOW)
    assert [c.action for c in found] == ["stop"]


def test_self_sent_mail_needs_no_spf_because_gmail_writes_none(store, gmail):
    """Measured 2026-09-16: Gmail adds no Authentication-Results to mail an
    account sends to itself, so an SPF/DKIM-only gate refused the operator's own
    command. The SENT label is the stronger proof — only the account holder can
    produce one."""
    gmail([_message("stop", auth="", labels=("INBOX", "UNREAD", "SENT"))])
    found = control.find_commands("tok", settings=Settings(), now=NOW)
    assert [c.action for c in found] == ["stop"]


def test_sent_label_exemption_does_not_extend_to_strangers(store, gmail):
    """An unauthenticated stranger must not benefit from the SENT exemption —
    the allowlist is checked first, and SENT is unforgeable from outside."""
    gmail([_message("stop", sender="attacker@evil.example.com", auth="",
                    labels=("INBOX", "UNREAD", "SENT"))])
    assert control.find_commands("tok", settings=Settings(), now=NOW) == []


def test_outgoing_mail_carries_the_marker_header():
    msg = notify.build_message([ME], ME, "Pup is out", "body")
    assert msg[notify.MARKER_HEADER] == "1"


def test_stale_command_is_skipped_and_consumed(store, gmail):
    fake = gmail([_message("start")])
    found = control.find_commands("tok", settings=Settings(), now=NOW + 3600)
    assert found == []
    assert fake.handled == ["m1"]


def test_date_header_without_a_timezone_is_read_as_utc(store, gmail):
    """A "-0000" Date parses naive; assuming local time skews staleness by the
    UTC offset, which on this laptop silently un-stales a 6h-old command."""
    msg = _message("start")
    for h in msg["payload"]["headers"]:
        if h["name"] == "Date":
            h["value"] = formatdate(NOW)  # ends in "-0000"
    fake = gmail([msg])
    assert control.find_commands("tok", settings=Settings(), now=NOW + 3600) == []
    assert fake.handled == ["m1"]


def test_partner_can_also_control(store, gmail):
    gmail([_message("stop", sender=PARTNER)])
    found = control.find_commands("tok", settings=Settings(), now=NOW)
    assert [(c.action, c.sender) for c in found] == [("stop", PARTNER)]


def test_no_allowlist_means_no_control(store, gmail, monkeypatch):
    monkeypatch.setenv("PUPWATCH_NOTIFY_TO", "")
    gmail([_message("stop")])
    assert control.find_commands("tok", settings=Settings(), now=NOW) == []


# --- applying commands -----------------------------------------------------

# --- the bug that made this feature not work at all --------------------------

def test_a_command_the_operator_already_read_is_still_found(store, gmail):
    """THE regression. Gmail marks mail you compose yourself as already read, so
    the operator's own replies arrive WITHOUT the UNREAD label. The original
    `is:unread` query therefore ignored every command he ever sent, silently —
    no ack, not even a rejection log. Observed 2026-09-16 on a real reply:
    labels ['IMPORTANT', 'SENT', 'INBOX'].
    """
    gmail([_message("start", labels=("IMPORTANT", "SENT", "INBOX"))])
    found = control.find_commands("tok", settings=Settings(), now=NOW)
    assert [c.action for c in found] == ["start"]


def test_discovery_does_not_filter_on_read_state(store, gmail):
    """Belt and braces: the query itself must never mention is:unread again."""
    fake = gmail([_message("start")])
    control.find_commands("tok", settings=Settings(), now=NOW)
    q = fake.queries[0]
    assert "unread" not in q.lower()
    assert f"-label%3A{control.HANDLED_LABEL}" in q or f"-label:{control.HANDLED_LABEL}" in q


def test_ordinary_mail_is_labelled_but_left_unread(store, gmail):
    """A normal email from him is not a command. Stop re-reading it every 60s,
    but do not touch its read state — it is his mail, not ours."""
    fake = gmail([_message("thanks, he looked happy!", subject="Re: something")])
    assert control.find_commands("tok", settings=Settings(), now=NOW) == []
    assert fake.handled == ["m1"]
    assert fake.marked_read == []


def test_our_own_sighting_mail_is_labelled_but_never_marked_read(store, gmail):
    """The unread badge IS the notification; clearing it would hide the alert."""
    fake = gmail([_message("Spotted at: 10:15", ours=True)])
    assert control.find_commands("tok", settings=Settings(), now=NOW) == []
    assert fake.handled == ["m1"]
    assert fake.marked_read == []


def test_handled_label_is_created_once_and_hidden(store, monkeypatch):
    calls = []

    def api(access, path, *, method="GET", payload=None):
        calls.append((path, method, payload))
        if path == "labels" and method == "GET":
            return {"labels": []}          # does not exist yet
        if path == "labels" and method == "POST":
            return {"id": "Label_new"}
        raise AssertionError(path)

    monkeypatch.setattr(control, "_api", api)
    assert control.handled_label_id("tok") == "Label_new"
    assert control.handled_label_id("tok") == "Label_new"   # cached
    creates = [c for c in calls if c[0] == "labels" and c[1] == "POST"]
    assert len(creates) == 1
    assert creates[0][2]["labelListVisibility"] == "labelHide"
    assert creates[0][2]["messageListVisibility"] == "hide"


# --- applying commands -------------------------------------------------------

def test_apply_start_opens_a_session(store):
    summary = control.apply(
        control.Command("start", 3.0, ME, "m1", "s", NOW), settings=Settings(), now=NOW)
    assert store["session"]["active"] is True
    assert store["session"]["stop_after_ts"] == NOW + 3 * 3600
    assert store["session"]["started_by"] == f"email:{ME}"
    assert "started" in summary


def test_bare_start_runs_until_an_explicit_stop(store):
    """Operator's requirement: "start should mean keep monitoring till I send
    stop". No end time, and session_max_hours must not quietly cut it off."""
    summary = control.apply(control.Command("start", None, ME, "m1", "s", NOW),
                            settings=Settings(), now=NOW)
    assert store["session"]["open_ended"] is True
    assert store["session"]["stop_after_ts"] is None
    assert "until you reply stop" in summary

    s = Settings()
    # Well past session_max_hours, which bounds only fixed-length sessions.
    later = NOW + (s.session_max_hours + 48) * 3600
    active, why = worker.session_active(store["session"], now=later, settings=s)
    assert (active, why) == (True, "active")


def test_open_ended_session_still_has_a_far_outer_bound(store):
    """A forgotten open-ended session polls every minute of every day, which
    does leave the free tier — so there is a last-resort stop."""
    control.apply(control.Command("start", None, ME, "m1", "s", NOW),
                  settings=Settings(), now=NOW)
    s = Settings()
    past = NOW + (s.session_absolute_max_hours + 1) * 3600
    active, why = worker.session_active(store["session"], now=past, settings=s)
    assert (active, why) == (False, "session_expired_absolute_max")


def test_hitting_the_outer_bound_emails_instead_of_going_quiet(store, monkeypatch):
    """Overruling "until I say stop" must be announced, not discovered as silence."""
    said = []
    monkeypatch.setattr(control, "announce", lambda summary, **k: said.append(summary))
    store["session"].update({"active": True, "open_ended": True, "started_ts": NOW,
                             "stop_after_ts": None})
    past = NOW + (Settings().session_absolute_max_hours + 1) * 3600
    out = worker.tick(now=past)
    assert out["reason"] == "session_expired_absolute_max"
    assert store["session"]["active"] is False
    assert said and "auto-stopped" in said[0] and "reply start" in said[0]


def test_every_automatic_stop_is_announced(store, monkeypatch):
    """Observed 2026-09-18: an open-ended session hit the 12h ceiling at 6am,
    stopped, and said nothing. The operator discovered it a day later, by which
    point his pup had been out in the yard unwatched. Silence is the failure —
    a stopped watcher looks exactly like a working one that has seen nothing.
    """
    said = []
    monkeypatch.setattr(control, "announce", lambda summary, **k: said.append(summary))
    store["session"].update({"active": True, "open_ended": False, "started_ts": NOW,
                             "stop_after_ts": NOW + 3600})
    out = worker.tick(now=NOW + 7200)          # an hour past its own end time
    assert out["reason"] == "session_expired_stop_after"
    assert store["session"]["active"] is False
    assert said and "reply start" in said[0]


def test_a_session_that_merely_ran_long_is_not_told_it_chose_a_window(store, monkeypatch):
    """The max_hours branch fires for sessions nobody gave a window to (including
    every session written before open_ended existed), so it must not claim one."""
    said = []
    monkeypatch.setattr(control, "announce", lambda summary, **k: said.append(summary))
    s = Settings()
    store["session"].update({"active": True, "started_ts": NOW, "stop_after_ts": None})
    out = worker.tick(now=NOW + (s.session_max_hours + 1) * 3600)
    assert out["reason"] == "session_expired_max_hours"
    assert said and "had been on for over" in said[0]
    assert "window" not in said[0]


def test_a_duration_is_still_honoured_and_bounded(store):
    summary = control.apply(control.Command("start", 4.0, ME, "m1", "s", NOW),
                            settings=Settings(), now=NOW)
    assert store["session"]["open_ended"] is False
    assert store["session"]["stop_after_ts"] == NOW + 4 * 3600
    assert "4h" in summary


def test_an_unparseable_duration_does_not_become_forever(store):
    """Absent duration means open-ended; a duration given wrong does not."""
    sessions.start(hours="soon", by="test", settings=Settings(), now=NOW)
    assert store["session"]["open_ended"] is False
    assert store["session"]["stop_after_ts"] == NOW + Settings().session_max_hours * 3600


def test_status_reports_an_open_ended_session_as_such(store):
    store["session"].update({"active": True, "open_ended": True, "stop_after_ts": None})
    out = control.apply(control.Command("status", None, ME, "m1", "s", NOW),
                        settings=Settings(), now=NOW)
    assert "monitoring is ON, until you reply stop" in out


def test_apply_start_is_capped_at_session_max_hours(store):
    control.apply(control.Command("start", 999.0, ME, "m1", "s", NOW),
                  settings=Settings(), now=NOW)
    assert store["session"]["stop_after_ts"] == NOW + Settings().session_max_hours * 3600


def test_apply_stop_closes_the_session(store):
    store["session"].update({"active": True})
    summary = control.apply(control.Command("stop", None, ME, "m1", "s", NOW),
                            settings=Settings(), now=NOW)
    assert store["session"]["active"] is False
    assert store["session"]["stopped_by"] == f"email:{ME}"
    assert summary == "monitoring stopped"


def test_apply_status_reports_off_then_on(store):
    off = control.apply(control.Command("status", None, ME, "m1", "s", NOW),
                        settings=Settings(), now=NOW)
    assert off == "monitoring is OFF"
    store["session"].update({"active": True, "stop_after_ts": NOW + 3600})
    on = control.apply(control.Command("status", None, ME, "m1", "s", NOW),
                       settings=Settings(), now=NOW)
    assert on.startswith("monitoring is ON")
    assert "no alert yet" in on


def test_apply_start_clears_the_previous_outings_cooldown(store):
    store["state"].update({"episode_active": True, "last_notified_ts": NOW - 60})
    control.apply(control.Command("start", None, ME, "m1", "s", NOW),
                  settings=Settings(), now=NOW)
    assert store["state"]["episode_active"] is False
    assert store["state"]["last_notified_ts"] is None


# --- end-to-end through poll_commands --------------------------------------

def test_poll_commands_consumes_exactly_once(store, gmail):
    fake = gmail([_message("start 2h")])
    applied = control.poll_commands(settings=Settings(), now=NOW)
    assert [a["action"] for a in applied] == ["start"]
    assert fake.handled == ["m1"]
    assert fake.marked_read == ["m1"]
    assert store["session"]["active"] is True


def test_poll_commands_disabled_by_config(store, gmail):
    fake = gmail([_message("start")])
    assert control.poll_commands(settings=Settings(control_email_enabled=False), now=NOW) == []
    assert store["session"] == {}


def test_poll_commands_survives_a_gmail_outage(store, monkeypatch):
    monkeypatch.setattr(notify, "access_token", lambda: "tok")
    monkeypatch.setattr(control, "_api", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert control.poll_commands(settings=Settings(), now=NOW) == []


def test_poll_commands_without_gmail_credentials_is_silent(store, monkeypatch):
    for k in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert control.poll_commands(settings=Settings(), now=NOW) == []


def test_failed_acknowledgement_does_not_undo_the_command(store, gmail, monkeypatch):
    gmail([_message("stop")])
    store["session"].update({"active": True})
    monkeypatch.setattr(control, "_confirm",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("smtp down")))
    # poll_commands documents that it never raises: one tick must not be aborted
    # by a failed acknowledgement, or a stop would look like it did not happen.
    applied = control.poll_commands(settings=Settings(), now=NOW)
    assert [a["action"] for a in applied] == ["stop"]
    # The stop already happened; the ack is the only thing that failed.
    assert store["session"]["active"] is False


def test_confirmation_goes_to_both_recipients(store, monkeypatch):
    sent = {}
    monkeypatch.setattr(notify, "gmail_send", lambda access, msg: sent.update({"msg": msg}))
    control._confirm("tok", control.Command("stop", None, ME, "m1", "s", NOW), "monitoring stopped")
    assert sent["msg"]["to"] == f"{ME}, {PARTNER}"
    assert "monitoring stopped" in sent["msg"]["subject"]


# --- the reason this runs before the session check -------------------------

def test_emailed_start_wakes_an_idle_tick(store, gmail, monkeypatch):
    """The whole point: "start" must work when nothing is running."""
    gmail([_message("start 1h")])
    monkeypatch.setattr(worker, "evaluate_camera",
                        lambda camera, settings: worker.CameraResult(camera=camera.name))
    out = worker.tick(now=NOW)
    assert [c["action"] for c in out["commands"]] == ["start"]
    assert out["polled"] is True          # the same tick already monitors
    assert store["session"]["active"] is True


def test_emailed_stop_takes_effect_on_the_same_tick(store, gmail):
    store["session"].update({"active": True, "started_ts": NOW, "stop_after_ts": NOW + 3600})
    gmail([_message("stop")])
    out = worker.tick(now=NOW)
    assert out["polled"] is False
    assert out["reason"] == "no_active_session"
