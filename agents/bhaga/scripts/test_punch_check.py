"""Missing-punch check (Issue #397): compose, gates, reply sweep, apply, ✅."""

from __future__ import annotations

import datetime as dt
import json
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

from agents.bhaga.scripts import punch_check as pc

CT = ZoneInfo("America/Chicago")
MON = dt.date(2026, 10, 12)

ALEX = {"date": MON, "employee_id": "Kim, Alex", "kind": "missing_out",
        "entries": [{"in": "09:02", "out": None}], "scheduled": [["09:00", "15:00"]],
        "open_entry_index": 0, "suggested_in": None, "suggested_out": "15:00"}
JORDAN = {"date": MON, "employee_id": "Lee, Jordan", "kind": "missing_in",
          "entries": [{"in": None, "out": "20:31"}], "scheduled": [["14:00", "20:30"]],
          "open_entry_index": 0, "suggested_in": "14:00", "suggested_out": None}
SAM = {"date": MON, "employee_id": "Ortiz, Sam", "kind": "no_entry", "entries": [],
       "scheduled": [["14:00", "20:30"]], "open_entry_index": None,
       "suggested_in": "14:00", "suggested_out": "20:30"}
PEOPLE = {"Kim, Alex": {"id": "11"}, "Lee, Jordan": {"id": "12"}, "Ortiz, Sam": {"id": "13"}}

APPROVED = """⏰ Missing punches · Tue 10/13
Reply in this thread by 9 PM.

Missing punch — we'll use your scheduled time
• [@Alex](#user_mention#11) · Mon 10/12 · Sched 9:00a–3:00p · In 9:02a, no out → out 3:00p
• [@Jordan](#user_mention#12) · Mon 10/12 · Sched 2:00p–8:30p · Out 8:31p, no in → in 2:00p

No punches — not paid until you confirm
• [@Sam](#user_mention#13) · Mon 10/12 · Sched 2:00p–8:30p

🙏 Clock in & out every shift."""


class TestCompose:
    def test_matches_the_approved_post(self):
        got = pc.compose(dt.date(2026, 10, 13), [SAM, JORDAN, ALEX], PEOPLE, pc.DEFAULT_FOOTER)
        assert got == APPROVED

    def test_sunday_names_monday_payroll(self):
        got = pc.compose(dt.date(2026, 10, 18), [ALEX], PEOPLE, pc.DEFAULT_FOOTER)
        assert got.splitlines()[1] == "Reply in this thread by 9 PM — payroll runs Monday morning."

    def test_unmatched_person_is_named_without_a_tag_and_empty_section_is_dropped(self):
        got = pc.compose(dt.date(2026, 10, 13), [ALEX], {}, pc.DEFAULT_FOOTER)
        assert "• Alex · Mon 10/12" in got and "No punches" not in got

    def test_shared_first_names_use_full_names(self):
        other = {**SAM, "employee_id": "Kim, Sam"}
        got = pc.compose(dt.date(2026, 10, 13), [SAM, other], {}, "")
        assert "• Sam Kim ·" in got and "• Sam Ortiz ·" in got

    def test_no_schedule_asks_for_the_time(self):
        bare = {**ALEX, "scheduled": [], "suggested_out": None}
        assert pc.item_line(bare, "Alex") == (
            "• Alex · Mon 10/12 · No shift scheduled · In 9:02a, no out → reply with your out time")


class TestGates:
    def at(self, day, hour, minute=0):
        return dt.datetime(2026, 10, day, hour, minute, tzinfo=CT)

    def test_sweeps(self):
        assert pc.sweep_due(self.at(12, 6)) == "payroll"       # Monday
        assert pc.sweep_due(self.at(12, 7)) is None            # draft already running
        assert pc.sweep_due(self.at(13, 6)) is None            # not Monday
        assert pc.sweep_due(self.at(13, 21)) == "evening"
        assert pc.sweep_due(self.at(13, 20, 45)) is None

    def test_post_due_from_the_configured_time(self):
        assert not pc.is_due(self.at(13, 9, 45), hour=10, minute=0, enabled=True)
        assert pc.is_due(self.at(13, 10), hour=10, minute=0, enabled=True)
        assert not pc.is_due(self.at(13, 11), hour=10, minute=0, enabled=False)


CFG = {"enabled": True, "hour_local": 10, "minute_local": 0, "timezone": "America/Chicago",
       "destination": "channel", "channel_id": "ch1", "workspace_id": "9", "template": pc.DEFAULT_FOOTER}


class TestPostDaily:
    NOW = dt.datetime(2026, 10, 13, 10, 0, tzinfo=CT)

    def _post(self, *, items, posted=False, **kw):
        sent, recorded, rows = [], [], []
        with mock.patch.object(pc.tp, "already_posted", return_value=posted), \
             mock.patch.object(pc, "open_items", return_value=items) as oi, \
             mock.patch.object(pc, "people_for", return_value=PEOPLE), \
             mock.patch.object(pc.tp, "record_post", lambda **k: recorded.append(k)), \
             mock.patch.object(pc, "_record_items", lambda *a: rows.append(a)), \
             mock.patch("core.datastore.ensure_schema"), \
             mock.patch("skills.clickup_chat.post_message",
                        lambda ch, content, team_id: sent.append((ch, content)) or {"id": "m1"}):
            result = pc.post_daily(dict(CFG), "palmetto", self.NOW, **kw)
        return result, sent, recorded, rows, oi

    def test_posts_once_to_the_channel_and_records_the_items(self):
        result, sent, recorded, rows, oi = self._post(items=[ALEX, SAM])
        assert result["status"] == "posted" and sent[0][0] == "ch1"
        assert oi.call_args.args == ("palmetto", dt.date(2026, 10, 12))
        assert recorded[0]["automation_id"] == "punch-check" and recorded[0]["message_id"] == "m1"
        assert rows[0][2] == "m1" and len(rows[0][4]) == 2

    def test_nothing_open_posts_nothing(self):
        result, sent, recorded, _rows, _ = self._post(items=[])
        assert (result["reason"], sent, recorded) == ("nothing open", [], [])

    def test_second_tick_same_day_posts_nothing(self):
        result, sent, *_ = self._post(items=[ALEX], posted=True)
        assert result["status"] == "skipped" and sent == []


def _gap(item):
    return {**item}


class TestReadReply:
    def _read(self, text, user="12", posted=None, items=(JORDAN,)):
        open_by_key = {(i["date"], i["employee_id"]): _gap(i) for i in items}
        posted = posted or {i["employee_id"]: [{"date": i["date"], "kind": i["kind"]}] for i in items}
        row, ds = pc.read_reply("palmetto", {"id": "r1", "user_id": user, "content": text}, "m1",
                                posted, open_by_key, {"11": "Kim, Alex", "12": "Lee, Jordan",
                                                      "13": "Ortiz, Sam"})
        return row, ds, open_by_key

    def test_understood_reply_becomes_a_staff_decision(self):
        row, (d,), open_by_key = self._read("in 2:05")
        assert row["outcome"] == "recorded" and json.loads(row["decision_ids_json"]) == [d["decision_id"]]
        assert (d["action"], d["in_time"], d["out_time"], d["status"]) == ("edit", "14:05", "20:31", "recorded")
        assert d["decided_by"] == "clickup:Jordan Lee" and d["open_entry_index"] == 0
        assert open_by_key == {}

    def test_dismissal_is_recorded_as_rejected(self):
        _row, (d,), _ = self._read("didn't work", user="13", items=(SAM,))
        assert (d["action"], d["status"]) == ("reject", "rejected")

    @pytest.mark.parametrize("text, user, outcome", [
        ("in 2:05", "99", "unknown_author"),
        ("whatever", "12", "not_understood"),
    ])
    def test_unresolved_replies_make_no_decision(self, text, user, outcome):
        row, ds, open_by_key = self._read(text, user=user)
        assert row["outcome"] == outcome and ds == [] and len(open_by_key) == 1

    def test_someone_else_replying_cannot_settle_jordans_punch(self):
        row, ds, _ = self._read("in 2:05", user="11", items=(JORDAN,),
                                posted={"Lee, Jordan": [{"date": MON, "kind": "missing_in"}]})
        assert row["outcome"] == "nothing_open" and ds == []

    def test_item_fixed_since_the_post_is_nothing_open(self):
        row, ds, _ = self._read("in 2:05", items=(),
                                posted={"Lee, Jordan": [{"date": MON, "kind": "missing_in"}]})
        assert row["outcome"] == "nothing_open"


class TestPayrollDefaults:
    def test_fills_missing_in_and_out_at_the_schedule_never_no_punch_days(self):
        bare = {**ALEX, "employee_id": "Roe, Pat", "scheduled": [], "suggested_out": None}
        rows = pc.payroll_defaults("palmetto", {(i["date"], i["employee_id"]): i
                                                for i in (ALEX, JORDAN, SAM, bare)})
        got = sorted((r["employee_id"], r["in_time"], r["out_time"]) for r in rows)
        assert got == [("Kim, Alex", None, "15:00"), ("Lee, Jordan", "14:00", "20:31")]
        assert {r["decided_by"] for r in rows} == {pc.PAYROLL_DEFAULT_BY}
        assert {r["action"] for r in rows} == {"accept"}


class TestSweep:
    def test_reads_new_replies_only_and_writes_decisions(self, monkeypatch):
        monkeypatch.delenv(pc.APPLY_FLAG, raising=False)
        inserted, upserted = [], []
        replies = [{"id": "old", "user_id": "12", "content": "in 2"},
                   {"id": "new", "user_id": "12", "content": "in 2:05"}]
        with mock.patch.object(pc, "_posted_items",
                               return_value={"m1": {"Lee, Jordan": [{"date": MON, "kind": "missing_in"}]}}), \
             mock.patch.object(pc, "_processed_reply_ids", return_value={"old"}), \
             mock.patch.object(pc, "open_items", return_value=[JORDAN, ALEX]), \
             mock.patch.object(pc, "people_for", return_value=PEOPLE), \
             mock.patch("skills.clickup_chat.fetch_replies", return_value=replies), \
             mock.patch.object(pc, "_insert_decisions", inserted.extend), \
             mock.patch.object(pc, "_upsert_replies", upserted.extend):
            result = pc.sweep_replies(dict(CFG), "palmetto", dt.datetime(2026, 10, 12, 6, tzinfo=CT),
                                      kind="payroll")
        assert [r["reply_id"] for r in upserted] == ["new"]
        assert [(d["employee_id"], d["in_time"]) for d in inserted] == [("Lee, Jordan", "14:05")]
        assert result["defaults"] == 0  # apply flag off: nothing auto-filled

    def test_payroll_sweep_defaults_the_rest_when_apply_is_on(self, monkeypatch):
        monkeypatch.setenv(pc.APPLY_FLAG, "1")
        inserted = []
        with mock.patch.object(pc, "_posted_items", return_value={}), \
             mock.patch.object(pc, "_processed_reply_ids", return_value=set()), \
             mock.patch.object(pc, "open_items", return_value=[ALEX, SAM]), \
             mock.patch.object(pc, "people_for", return_value={}), \
             mock.patch.object(pc, "_insert_decisions", inserted.extend), \
             mock.patch.object(pc, "_upsert_replies"):
            result = pc.sweep_replies(dict(CFG), "palmetto", dt.datetime(2026, 10, 12, 6, tzinfo=CT),
                                      kind="payroll")
        assert result["defaults"] == 1 and inserted[0]["employee_id"] == "Kim, Alex"


class TestReactions:
    @pytest.mark.parametrize("statuses, want", [
        (["applied"], "done"),
        (["applied", "already_resolved"], "done"),
        (["rejected"], "done"),
        (["not_in_hours"], "done"),
        (["applied", "failed"], "failed"),
        (["applying"], None),
        (["recorded"], None),
        ([], None),
    ])
    def test_reply_outcome(self, statuses, want):
        assert pc.reply_outcome(statuses) == want

    def test_done_reply_gets_a_check_and_failed_reply_none(self):
        rows = [{"reply_id": "r1", "statuses": ["applied"]}, {"reply_id": "r2", "statuses": ["failed"]},
                {"reply_id": "r3", "statuses": ["applying"]}]
        reacted, upserted = [], []
        with mock.patch("core.datastore.read_query", return_value=rows), \
             mock.patch("skills.clickup_chat.add_reaction", lambda rid, team_id: reacted.append(rid)), \
             mock.patch.object(pc, "_upsert_replies", upserted.extend):
            result = pc.react_done("palmetto", "9")
        assert reacted == ["r1"] and result == {"reacted": 1, "failed": 1}
        assert [(u["reply_id"], u["outcome"]) for u in upserted] == [("r1", "done"), ("r2", "failed")]


class _Job:
    def __init__(self, rows=()):
        self._rows = rows

    def result(self):
        return list(self._rows)


class _Client:
    def __init__(self, ids):
        self.ids = ids
        self.sql = []

    def query(self, sql, job_config=None):
        self.sql.append(sql)
        return _Job([{"decision_id": i} for i in self.ids] if sql.lstrip().startswith("SELECT") else ())


class TestApplyPending:
    def test_busy_job_releases_the_claim_for_the_next_tick(self):
        client = _Client(["d1"])
        with mock.patch("core.datastore.get_client", return_value=client):
            result = pc.apply_pending("palmetto", lambda ids: False)
        assert result == {"status": "busy", "decisions": 1}
        assert "SET status = 'recorded'" in client.sql[-1]

    def test_claimed_decisions_start_the_job_once(self):
        client = _Client(["d1", "d2"])
        started = []
        with mock.patch("core.datastore.get_client", return_value=client):
            result = pc.apply_pending("palmetto", lambda ids: started.append(ids) or True)
        assert started == [["d1", "d2"]] and result["status"] == "started"
        assert "decided_by LIKE @staff OR d.decided_by = @auto" in client.sql[0]

    def test_nothing_claimed_starts_nothing(self):
        with mock.patch("core.datastore.get_client", return_value=_Client([])):
            assert pc.apply_pending("palmetto", lambda ids: 1 / 0) == {"status": "nothing_to_apply"}


class TestTick:
    def test_evening_sweep_runs_once_per_day(self, monkeypatch):
        monkeypatch.delenv(pc.APPLY_FLAG, raising=False)
        ledger = []
        with mock.patch.object(pc, "ensure_default_config", return_value=dict(CFG)), \
             mock.patch.object(pc, "post_daily", return_value={"status": "skipped"}), \
             mock.patch.object(pc.tp, "already_posted", side_effect=lambda s, a, d: a in ledger), \
             mock.patch.object(pc.tp, "record_post", lambda **k: ledger.append(k["automation_id"])), \
             mock.patch.object(pc, "sweep_replies", return_value={"status": "swept"}) as sweep, \
             mock.patch.object(pc, "react_done", return_value={}):
            now = dt.datetime(2026, 10, 13, 21, 0, tzinfo=CT)
            pc.run_tick(now=now)
            pc.run_tick(now=now + dt.timedelta(minutes=15))
        assert sweep.call_count == 1 and ledger == ["punch-check:sweep-evening"]
