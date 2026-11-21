"""Tasks API: access rules, daily lists, ticks, counts, alerts, ClickUp import, shared shifts."""

from __future__ import annotations

import datetime
import io
import json
import pathlib

import pytest

from skills.google_signin import identity

from agents.bhaga.scripts.ingest_inventory import rows_from_task
from cloud.store_tasks import app as app_mod
from cloud.store_tasks import auth, clickup_import as ci, events as ev, model, service as svc
from cloud.store_tasks import shared_sync, store as st, verify_backfill

TZ = datetime.timezone(datetime.timedelta(hours=-6))
INTERNAL = "internal-token-for-tests-0123456789"
IPAD = "ipad@store.example"
OWNER = "owner@store.example"
FIELD = "f-acai"


class Clock:
    def __init__(self):
        self.at = datetime.datetime(2026, 11, 19, 9, 0, tzinfo=TZ)  # a Thursday

    def __call__(self):
        return self.at


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("STORE_TASKS_SIGNING_KEY", "k" * 40)
    monkeypatch.setenv("STORE_TASKS_INTERNAL_TOKEN", INTERNAL)
    monkeypatch.delenv("CLICKUP_PAT", raising=False)  # tests never reach ClickUp
    clock = Clock()
    tasks = svc.Tasks(st.MemoryStore(), st.DirPhotoStore(str(tmp_path)), ev.Events(topic=""), clock)
    cfg = json.loads((pathlib.Path(__file__).parent / "stores" / "palmetto.json").read_text())
    cfg.update(cutover_date="2026-11-19", count_items=[{"id": FIELD, "name": "Açaí", "kind": "text"}])
    tasks.put_store(model.StoreConfig.from_dict(cfg))
    for data in (
        {"id": "t1", "shift": "opening", "title": "Unlock", "order": 1},
        {"id": "t2", "shift": "opening", "title": "Deep Clean", "order": 2},
        {"id": "t3", "shift": "opening", "title": "Thu: blender", "order": 1, "parent_id": "t2",
         "weekdays": [3], "needs_photo": True},
        {"id": "t4", "shift": "opening", "title": "Fri: shelf", "order": 2, "parent_id": "t2", "weekdays": [4]},
        {"id": "t5", "shift": "closing", "title": "Mop", "order": 1},
    ):
        tasks.save_template("palmetto", data, "test")
    crew = tasks.save_staff("palmetto", {"name": "Dolce Johnson"}, "test")
    boss = tasks.save_staff("palmetto", {"name": "Lee Manager", "role": "manager"}, "test")
    tasks.set_pin("palmetto", crew["id"], "1234")
    tasks.set_pin("palmetto", boss["id"], "123456")
    tasks.save_account("palmetto", {"email": IPAD, "label": "Front iPad"}, "test")
    tasks.save_account("palmetto", {"email": OWNER, "staff_id": boss["id"]}, "test")
    grants = []

    def signed_in(headers):
        if not headers.get("X-Test-Google"):
            raise identity.NotSignedIn("no Google sign-in on this request")
        return headers["X-Test-Google"]

    fake_access = type("Access", (), {"set": lambda self, email, allowed: grants.append((email, allowed)) or "granted"})()
    client = app_mod.create_app(tasks, signed_in=signed_in, signin_access=fake_access).test_client()
    ipad = {"X-Test-Google": IPAD}

    def sign_in(staff, pin):
        r = client.post("/v1/sessions", json={"staff_id": staff["id"], "pin": pin}, headers=ipad)
        return {**ipad, "X-Staff-Session": r.get_json()["session"]}

    return type("Env", (), dict(tasks=tasks, client=client, clock=clock, ipad=ipad, crew=crew, boss=boss,
                                grants=grants, crew_h=sign_in(crew, "1234"), boss_h=sign_in(boss, "123456")))


def test_access_needs_an_allowed_google_account_then_a_session_then_a_role(env):
    c = env.client
    assert c.get("/v1/bootstrap").status_code == 401  # no Google sign-in
    assert c.get("/v1/bootstrap", headers={"X-Test-Google": "stranger@gmail.com"}).status_code == 403
    assert c.get("/v1/days/2026-11-19", headers=env.ipad).status_code == 401  # shared account, no PIN yet
    assert c.get("/v1/templates", headers=env.crew_h).status_code == 403
    assert c.get("/v1/templates", headers=env.boss_h).status_code == 200
    assert c.post("/internal/stores/palmetto/check-shifts").status_code == 401
    forged = {**env.crew_h, "X-Staff-Session": env.crew_h["X-Staff-Session"][:-2] + "xx"}
    assert c.get("/v1/days/2026-11-19", headers=forged).status_code == 401
    # A session made on one Google account is useless on another.
    assert c.get("/v1/days/2026-11-19", headers={**env.crew_h, "X-Test-Google": OWNER}).status_code == 401


def test_own_google_account_signs_a_person_in_without_a_pin(env):
    boot = env.client.get("/v1/bootstrap", headers={"X-Test-Google": OWNER}).get_json()
    assert boot["own"]["staff"]["id"] == env.boss["id"]
    h = {"X-Test-Google": OWNER, "X-Staff-Session": boot["own"]["session"]}
    assert env.client.get("/v1/templates", headers=h).status_code == 200
    assert env.client.get("/v1/bootstrap", headers=env.ipad).get_json()["own"] is None


def test_manager_adds_and_removes_google_accounts(env):
    c = env.client
    assert c.post("/v1/accounts", json={"email": "New@Gmail.com"}, headers=env.crew_h).status_code == 403
    assert c.post("/v1/accounts", json={"email": "New@Gmail.com", "label": "Back iPad"}, headers=env.boss_h).status_code == 200
    assert env.grants == [("new@gmail.com", True)]
    assert c.get("/v1/bootstrap", headers={"X-Test-Google": "new@gmail.com"}).status_code == 200
    assert c.delete(f"/v1/accounts/{IPAD}", headers=env.boss_h).status_code == 400  # the one in use
    assert c.delete("/v1/accounts/new@gmail.com", headers=env.boss_h).status_code == 200
    assert env.grants[-1] == ("new@gmail.com", False)
    assert c.get("/v1/bootstrap", headers={"X-Test-Google": "new@gmail.com"}).status_code == 403


def test_bootstrap_never_leaks_pin_material(env):
    body = env.client.get("/v1/bootstrap", headers=env.ipad).get_data(as_text=True)
    assert "pin_hash" not in body and "pin_salt" not in body and "Dolce Johnson" in body


def test_wrong_pin_locks_after_five_misses(env):
    h = env.ipad
    for _ in range(auth.MAX_PIN_MISSES):
        assert env.client.post("/v1/sessions", json={"staff_id": env.crew["id"], "pin": "0000"},
                               headers=h).status_code == 403
    assert env.client.post("/v1/sessions", json={"staff_id": env.crew["id"], "pin": "1234"},
                           headers=h).status_code == 429
    env.clock.at += datetime.timedelta(seconds=auth.LOCK_SECONDS + 1)
    assert env.client.post("/v1/sessions", json={"staff_id": env.crew["id"], "pin": "1234"},
                           headers=h).status_code == 200


def test_day_is_generated_once_with_only_todays_deep_clean(env):
    day = env.client.get("/v1/days/2026-11-19", headers=env.crew_h).get_json()
    assert sorted(t["title"] for t in day["tasks"]) == ["Deep Clean", "Mop", "Thu: blender", "Unlock"]
    again = env.client.get("/v1/days/2026-11-19", headers=env.crew_h).get_json()
    assert [t["id"] for t in again["tasks"]] == [t["id"] for t in day["tasks"]]
    assert env.tasks.docs.count(st.TASKS, store="palmetto", date="2026-11-19") == 4


def test_tick_records_who_and_is_idempotent_and_undo_is_limited(env):
    tid = "palmetto_2026-11-19_t1"
    env.client.get("/v1/days/2026-11-19", headers=env.crew_h)
    first = env.client.post(f"/v1/tasks/{tid}/complete", json={"note": "ok"}, headers=env.crew_h).get_json()
    env.clock.at += datetime.timedelta(minutes=5)
    second = env.client.post(f"/v1/tasks/{tid}/complete", json={}, headers=env.boss_h).get_json()
    assert first["completed_by"] == env.crew["id"] == second["completed_by"]
    assert first["completed_at"] == second["completed_at"]
    assert [e["type"] for e in env.tasks.events.sent] == [ev.TASK_COMPLETED]
    other = env.tasks.save_staff("palmetto", {"name": "Other Person"}, "test")
    assert env.tasks.reopen("palmetto", tid, {"sid": env.crew["id"], "role": "crew"})["status"] == model.OPEN
    env.tasks.complete("palmetto", tid, {"sid": env.crew["id"], "role": "crew"})
    with pytest.raises(auth.AuthError):
        env.tasks.reopen("palmetto", tid, {"sid": other["id"], "role": "crew"})


def test_photo_task_needs_a_photo(env):
    tid = "palmetto_2026-11-19_t3"
    env.client.get("/v1/days/2026-11-19", headers=env.crew_h)
    assert env.client.post(f"/v1/tasks/{tid}/complete", json={}, headers=env.crew_h).status_code == 400
    up = env.client.post(f"/v1/tasks/{tid}/photo", headers=env.crew_h,
                         data={"photo": (io.BytesIO(b"\xff\xd8jpeg"), "a.jpg", "image/jpeg")})
    path = up.get_json()["photos"][0]["path"]
    assert env.client.get(f"/v1/photos/{path}", headers=env.crew_h).data == b"\xff\xd8jpeg"
    assert env.client.get("/v1/photos/houston/x", headers=env.crew_h).status_code == 404
    assert env.client.post(f"/v1/tasks/{tid}/complete", json={}, headers=env.crew_h).status_code == 200


JPEG = b"\xff\xd8jpeg"


def _upload(env, url, headers):
    return env.client.post(url, headers=headers, data={"photo": (io.BytesIO(JPEG), "a.jpg", "image/jpeg")})


def test_anyone_assigns_any_person_to_one_or_many_tasks(env):
    c = env.client
    ids = [t["id"] for t in c.get("/v1/days/2026-11-19", headers=env.crew_h).get_json()["tasks"]]
    r = c.post("/v1/assign", json={"task_ids": ids[:2], "assignee_id": env.boss["id"]}, headers=env.crew_h)
    assert [t["assignee_id"] for t in r.get_json()["tasks"]] == [env.boss["id"]] * 2
    day = c.get("/v1/days/2026-11-19", headers=env.crew_h).get_json()["tasks"]
    assert sum(1 for t in day if t["assignee_id"] == env.boss["id"]) == 2
    c.post("/v1/assign", json={"task_ids": ids[:1], "assignee_id": None}, headers=env.crew_h)
    assert c.get(f"/v1/tasks/{ids[0]}", headers=env.crew_h).get_json()["task"]["assignee_id"] is None
    assert c.post("/v1/assign", json={"task_ids": ids[:1], "assignee_id": "nobody"}, headers=env.crew_h).status_code == 404
    assert c.post("/v1/assign", json={"task_ids": [], "assignee_id": None}, headers=env.crew_h).status_code == 400


def test_task_detail_has_how_to_example_photos_and_earlier_days_photos(env):
    c = env.client
    tid = "palmetto_2026-11-19_t1"
    c.get("/v1/days/2026-11-19", headers=env.crew_h)
    # Any task takes a photo, not only the ones that require it.
    path = _upload(env, f"/v1/tasks/{tid}/photo", env.crew_h).get_json()["photos"][0]["path"]
    assert _upload(env, f"/v1/tasks/{tid}/reference-photo", env.crew_h).status_code == 403
    ref = _upload(env, f"/v1/tasks/{tid}/reference-photo", env.boss_h).get_json()["reference_photos"]
    assert len(ref) == 1 and c.get(f"/v1/photos/{ref[0]['path']}", headers=env.crew_h).data == JPEG
    assert c.post(f"/v1/tasks/{tid}/instructions", json={"instructions": "x"}, headers=env.crew_h).status_code == 403
    c.post(f"/v1/tasks/{tid}/instructions", json={"instructions": "Key is in the safe"}, headers=env.boss_h)

    today = c.get(f"/v1/tasks/{tid}", headers=env.crew_h).get_json()
    assert today["task"]["instructions"] == "Key is in the safe"
    assert today["recent_photos"] == []  # today's own photo is not an earlier one

    env.clock.at += datetime.timedelta(hours=20)  # the next morning: yesterday's sessions have run out
    def session(staff, pin):
        r = c.post("/v1/sessions", json={"staff_id": staff["id"], "pin": pin}, headers=env.ipad)
        return {**env.ipad, "X-Staff-Session": r.get_json()["session"]}
    env.crew_h, env.boss_h = session(env.crew, "1234"), session(env.boss, "123456")
    c.get("/v1/days/2026-11-20", headers=env.crew_h)
    tomorrow = c.get("/v1/tasks/palmetto_2026-11-20_t1", headers=env.crew_h).get_json()
    assert tomorrow["task"]["instructions"] == "Key is in the safe"
    assert [p["path"] for p in tomorrow["reference_photos"]] == [ref[0]["path"]]
    assert [(p["path"], p["date"]) for p in tomorrow["recent_photos"]] == [(path, "2026-11-19")]

    # Only the person who added a photo, or a manager, removes it.
    other = env.tasks.save_staff("palmetto", {"name": "Other Crew"}, "test")
    env.tasks.set_pin("palmetto", other["id"], "4321")
    other_h = session(other, "4321")
    assert c.delete(f"/v1/tasks/{tid}/photos/{path}", headers=other_h).status_code == 403
    assert c.delete(f"/v1/tasks/{tid}/photos/{path}", headers=env.crew_h).get_json()["photos"] == []
    assert c.get("/v1/tasks/palmetto_2026-11-20_t1", headers=env.crew_h).get_json()["recent_photos"] == []
    assert c.delete(f"/v1/tasks/{tid}/reference-photos/{ref[0]['path']}", headers=env.boss_h).get_json()["reference_photos"] == []


def test_added_task_takes_an_assignee_and_only_its_author_or_a_manager_deletes_it(env):
    c = env.client
    t = c.post("/v1/tasks", json={"title": "Call plumber", "shift": "closing", "date": "2026-11-19",
                                  "assignee_id": env.boss["id"]}, headers=env.boss_h).get_json()
    assert t["assignee_id"] == env.boss["id"] and t["created_by"] == env.boss["id"]
    assert c.delete(f"/v1/tasks/{t['id']}", headers=env.crew_h).status_code == 403
    c.get("/v1/days/2026-11-19", headers=env.crew_h)
    assert c.delete("/v1/tasks/palmetto_2026-11-19_t1", headers=env.boss_h).status_code == 400  # recurring
    assert c.delete(f"/v1/tasks/{t['id']}", headers=env.boss_h).status_code == 200
    titles = [x["title"] for x in c.get("/v1/days/2026-11-19", headers=env.crew_h).get_json()["tasks"]]
    assert "Call plumber" not in titles and "Unlock" in titles


def test_comments_are_kept_in_order_with_who_wrote_them(env):
    c = env.client
    tid = "palmetto_2026-11-19_t1"
    c.get("/v1/days/2026-11-19", headers=env.crew_h)
    assert c.post(f"/v1/tasks/{tid}/comments", json={"text": "  "}, headers=env.crew_h).status_code == 400
    c.post(f"/v1/tasks/{tid}/comments", json={"text": "Door sticks"}, headers=env.crew_h)
    got = c.post(f"/v1/tasks/{tid}/comments", json={"text": "Fixed"}, headers=env.boss_h).get_json()["comments"]
    assert [(m["text"], m["by"]) for m in got] == [("Door sticks", env.crew["id"]), ("Fixed", env.boss["id"])]


def test_closing_count_photos_survive_sending_the_count(env):
    c = env.client
    up = _upload(env, "/v1/counts/2026-11-19/photo", env.crew_h).get_json()["count"]
    assert len(up["photos"]) == 1
    assert c.get("/v1/days/2026-11-19", headers=env.crew_h).get_json()["count_submitted"] is False
    sent = c.put("/v1/counts/2026-11-19", json={"items": {FIELD: "2"}}, headers=env.crew_h).get_json()["count"]
    assert len(sent["photos"]) == 1 and sent["revisions"] == []
    assert c.get("/v1/days/2026-11-19", headers=env.crew_h).get_json()["count_submitted"] is True


def test_closing_count_feeds_inventory_in_clickup_shape(env):
    env.clock.at = datetime.datetime(2026, 11, 19, 20, 15, tzinfo=TZ)
    r = env.client.put("/v1/counts/2026-11-19", headers=env.crew_h,
                       json={"items": {FIELD: "38+20%", "unknown": "1"}, "notes": {"shift": "slow"}})
    assert r.get_json()["count"]["items"] == {FIELD: "38+20%"}
    env.client.put("/v1/counts/2026-11-19", headers=env.crew_h, json={"items": {FIELD: "39"}})
    out = env.client.get("/internal/stores/palmetto/closing-counts",
                         headers={"Authorization": f"Bearer {INTERNAL}"}).get_json()["tasks"]
    assert len(out) == 1 and out[0]["name"].startswith("Form Submission - #2026-11-19T20:15:00")
    assert out[0]["custom_fields"] == [{"id": FIELD, "name": "Açaí", "value": "39"}]
    assert len(env.tasks.count("palmetto", datetime.date(2026, 11, 19))["revisions"]) == 1
    rows = rows_from_task(out[0], "palmetto", run_id="test")  # the nightly inventory parser, unchanged
    assert [(r["field_id"], r["submitted_date"], r["source_task_id"]) for r in rows] == [
        (FIELD, "2026-11-19", "st-palmetto_2026-11-19")]


def test_unfinished_shift_alerts_once(env):
    h = {"Authorization": f"Bearer {INTERNAL}"}
    env.client.get("/v1/days/2026-11-19", headers=env.crew_h)
    env.clock.at = datetime.datetime(2026, 11, 19, 7, 20, tzinfo=TZ)
    assert env.client.post("/internal/stores/palmetto/check-shifts", headers=h).get_json()["alerts"] == []
    env.clock.at = datetime.datetime(2026, 11, 19, 7, 31, tzinfo=TZ)
    alerts = env.client.post("/internal/stores/palmetto/check-shifts", headers=h).get_json()["alerts"]
    assert [(a["shift"], a["open"], a["total"]) for a in alerts] == [("opening", 3, 3)]
    assert env.client.post("/internal/stores/palmetto/check-shifts", headers=h).get_json()["alerts"] == []


def test_tick_runs_hourly_jobs_once_per_hour(env, monkeypatch):
    runs = []
    monkeypatch.setattr(shared_sync, "sync", lambda tasks, sid: runs.append(sid) or {"days": 1})
    h = {"Authorization": f"Bearer {INTERNAL}"}
    env.client.post("/internal/tick", headers=h)
    env.clock.at += datetime.timedelta(minutes=10)
    env.client.post("/internal/tick", headers=h)
    env.clock.at += datetime.timedelta(minutes=55)
    out = env.client.post("/internal/tick", headers=h).get_json()
    assert runs == ["palmetto", "palmetto"] and "mirror" not in out["palmetto"]

    monkeypatch.setenv("CLICKUP_PAT", "x")
    monkeypatch.setattr(ci, "mirror", lambda tasks, sid: {"tasks": 3})
    assert env.client.post("/internal/tick", headers=h).get_json()["palmetto"]["mirror"] == {"tasks": 3}


def test_internal_one_off_is_idempotent_by_key(env):
    h = {"Authorization": f"Bearer {INTERNAL}"}
    body = {"title": "Freezer door sticks", "shift": "closing", "source": "slack", "key": "C1-171.5"}
    a = env.client.post("/internal/stores/palmetto/tasks", json=body, headers=h).get_json()
    b = env.client.post("/internal/stores/palmetto/tasks", json=body, headers=h).get_json()
    assert a["id"] == b["id"] and (a["shift"], a["date"], a["source"]) == ("closing", "2026-11-19", "slack")
    assert [e["type"] for e in env.tasks.events.sent] == [ev.TASK_CREATED]


def test_template_edit_keeps_history(env):
    env.client.post("/v1/templates", headers=env.boss_h,
                    json={"id": "t1", "shift": "opening", "title": "Unlock doors", "order": 1})
    assert [h["title"] for h in env.tasks.docs.query(st.TEMPLATE_HISTORY, template_id="t1")] == ["Unlock"]


def test_shift_team_comes_from_shared_schedule(env):
    rows = [{"date": "2026-11-19", "name": "Johnson, Dolce J", "ranges": ["6:30 AM - 2:30 PM"]},
            {"date": "2026-11-19", "name": "Vu, Phong", "ranges": ["3:00 PM - 8:30 PM"]}]
    out = shared_sync.sync(env.tasks, "palmetto", read=lambda ds, a, b: rows)
    assert out["days"] == shared_sync.DAYS_BACK + shared_sync.DAYS_AHEAD + 1
    teams = env.client.get("/v1/days/2026-11-19", headers=env.crew_h).get_json()["teams"]
    assert teams["opening"] == [{"id": env.crew["id"], "name": "Dolce Johnson"}]
    assert teams["shift_change"] == [{"id": env.crew["id"], "name": "Dolce Johnson"}]
    assert teams["closing"] == [{"id": None, "name": "Vu, Phong"}]


# ── ClickUp import ─────────────────────────────────────────────────────────


def _ms(*a):
    return str(int(datetime.datetime(*a, tzinfo=TZ).timestamp() * 1000))


def _cu(i, name, day, **kw):
    return {"id": i, "name": name, "text_content": kw.get("text", ""), "status": {"type": kw.get("st", "open")},
            "date_created": _ms(2026, 11, day, 1), "date_updated": _ms(2026, 11, day, 2),
            "start_date": None if kw.get("undated") else _ms(2026, 11, day, 4),
            "due_date": None if kw.get("undated") else _ms(2026, 11, day, kw.get("hour", 4)),
            "date_closed": _ms(2026, 11, day, 8) if kw.get("st") == "closed" else None,
            "assignees": [{"id": 7}], "tags": [{"name": n} for n in kw.get("tags", [])],
            "parent": kw.get("parent"), "custom_fields": kw.get("fields", [{"name": "Order #", "value": "3"}])}


def test_import_maps_fields_days_and_infers_templates(env):
    cfg = env.tasks.store("palmetto")
    raw = {"opening": [], "closing": []}
    for day in range(2, 20):
        raw["opening"] += [
            _cu(f"a{day}", "Unlock", day, st="closed", text="Front door"),
            _cu(f"p{day}", "Deep Clean", day),
            _cu(f"s{day}", "Thu: blender", day, parent=f"p{day}", undated=True),
        ]
    raw["opening"].append(_cu("once", "One-off", 10))
    raw["closing"].append({**_cu("form", "Form Submission - #2026-11-18T20:10:00-06:00", 18,
                                 tags=["closing submission"],
                                 fields=[{"id": FIELD, "name": "Açaí", "value": "38+20%"},
                                         {"id": "n", "name": "Shift notes", "value": "slow"}]),
                           "date_created": _ms(2026, 11, 18, 20, 10)})
    docs, counts = ci.split(cfg, raw)
    a = next(d for d in docs if d["id"] == "cu_a5")
    assert (a["date"], a["status"], a["order"], a["instructions"]) == ("2026-11-05", "done", 3.0, "Front door")
    assert a["due_at"].startswith("2026-11-05T07:00") and a["completed_by"] == "cu7"
    assert next(d for d in docs if d["id"] == "cu_s5")["date"] == "2026-11-05"
    assert counts[0]["items"] == {FIELD: "38+20%"} and counts[0]["notes"]["shift"] == "slow"

    templates = ci.infer_templates(cfg, docs, datetime.date(2026, 11, 19))
    assert sorted((t.title, t.weekdays, bool(t.parent_id)) for t in templates) == [
        ("Deep Clean", (), False), ("Thu: blender", (3,), True), ("Unlock", (), False)]

    # Days at or after cutover belong to this service: the mirror must not write them.
    assert ci.write_tasks(env.tasks, cfg, docs) == sum(1 for d in docs if d["date"] < "2026-11-19")
    assert ci.write_tasks(env.tasks, cfg, docs) == env.tasks.docs.count(st.TASKS, source="clickup")
    assert ci.write_counts(env.tasks, counts) == 1

    report = verify_backfill.verify(env.tasks, "palmetto", raw)
    assert report["ok"], report

    # A ClickUp copy left on a day this service owns stays stored but is never shown or counted.
    env.tasks.docs.set(st.TASKS, "cu_a19", next(d for d in docs if d["id"] == "cu_a19"))
    shown = env.client.get("/v1/days/2026-11-19", headers=env.crew_h).get_json()["tasks"]
    assert shown and all(t["source"] == "generated" for t in shown)
    before = env.client.get("/v1/days/2026-11-18", headers=env.crew_h).get_json()
    assert not before["generated"] and {t["source"] for t in before["tasks"]} == {"clickup"}
    assert env.client.post("/v1/tasks/cu_p18/complete", json={}, headers=env.crew_h).status_code == 400
    assert verify_backfill.verify(env.tasks, "palmetto", raw)["ok"]
    env.tasks.docs.set(st.TASKS, "cu_a5", {"status": "open"}, merge=True)
    env.tasks.docs.delete(st.TASKS, "cu_a6")
    broken = verify_backfill.verify(env.tasks, "palmetto", raw)
    assert not broken["ok"] and broken["missing"] == ["cu_a6"] and ("cu_a5", "status") in broken["field_mismatches"]
