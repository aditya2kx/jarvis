"""Tasks API: access rules, daily lists, ticks, counts, alerts, ClickUp import, shared shifts."""

from __future__ import annotations

import dataclasses
import datetime
import io
import json
import pathlib

import pytest

from skills.google_signin import identity

from agents.bhaga.scripts.ingest_inventory import rows_from_task
from cloud.store_tasks import app as app_mod
from cloud.store_tasks import auth, clickup_import as ci, events as ev, model, service as svc
from cloud.store_tasks import shared_sync, store as st, summaries, verify_backfill

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


def test_shop_device_signs_in_with_the_store_code_and_ticks_for_the_assignee(env):
    c = env.client
    assert c.put("/v1/shop-code", json={"code": "482913"}, headers=env.crew_h).status_code == 403
    assert c.put("/v1/shop-code", json={"code": "4829"}, headers=env.boss_h).status_code == 400
    assert c.put("/v1/shop-code", json={"code": "482913"}, headers=env.boss_h).get_json()["set"] is True
    assert c.post("/v1/shop/enroll", json={"code": "000000"}).status_code == 403
    device = c.post("/v1/shop/enroll", json={"code": "482913"}).get_json()["device"]
    shop = {"X-Shop-Device": device}
    boot = c.get("/v1/bootstrap", headers=shop).get_json()  # no Google account at all
    assert boot["account"]["shop"] is True and boot["own"]["staff"]["id"] == auth.SHOP and boot["device"]
    h = {**shop, "X-Staff-Session": boot["own"]["session"]}
    assert c.get("/v1/templates", headers=h).status_code == 403
    # The shop ticks, photographs and comments; adding tasks waits for a manager.
    assert c.post("/v1/tasks", json={"title": "Extra", "date": "2026-11-19"}, headers=h).status_code == 403
    tid = "palmetto_2026-11-19_t1"
    c.get("/v1/days/2026-11-19", headers=h)
    c.post("/v1/assign", json={"task_ids": [tid], "assignee_id": env.crew["id"]}, headers=h)
    done = c.post(f"/v1/tasks/{tid}/complete", json={}, headers=h).get_json()
    assert done["completed_by"] == env.crew["id"] and done["ticked_by"] == auth.SHOP
    assert c.post(f"/v1/tasks/{tid}/reopen", headers=h).get_json()["status"] == "open"
    # Only managers sign in on the shop device; crew just use it.
    assert c.post("/v1/sessions", json={"staff_id": env.crew["id"], "pin": "1234"}, headers=shop).status_code == 403
    boss = c.post("/v1/sessions", json={"staff_id": env.boss["id"], "pin": "123456"}, headers=shop).get_json()
    boss_on_shop = {**shop, "X-Staff-Session": boss["session"]}
    assert c.get("/v1/templates", headers=boss_on_shop).status_code == 200
    assert c.post("/v1/tasks", json={"title": "Extra", "date": "2026-11-19"}, headers=boss_on_shop).status_code == 200
    # The shop session is no Google session and no staff session is a device token.
    assert c.get("/v1/bootstrap", headers={"X-Shop-Device": env.crew_h["X-Staff-Session"]}).status_code == 401
    assert c.get("/v1/days/2026-11-19", headers={**env.ipad, "X-Staff-Session": device}).status_code == 401
    # A new code signs old devices out.
    c.put("/v1/shop-code", json={"code": "771204"}, headers=env.boss_h)
    assert c.get("/v1/bootstrap", headers=shop).status_code == 401


def test_wrong_shop_codes_pause_shop_sign_in(env):
    env.tasks.set_shop_code("palmetto", "482913", "test")
    assert env.client.post("/v1/shop/enroll", json={"code": "111111"}).status_code == 403
    env.clock.at += datetime.timedelta(seconds=auth.CODE_LOCK_SECONDS + 1)  # an old miss no longer counts
    for _ in range(auth.MAX_CODE_MISSES):
        assert env.client.post("/v1/shop/enroll", json={"code": "111111"}).status_code == 403
    assert env.client.post("/v1/shop/enroll", json={"code": "482913"}).status_code == 429
    env.clock.at += datetime.timedelta(seconds=auth.CODE_LOCK_SECONDS + 1)
    assert env.client.post("/v1/shop/enroll", json={"code": "482913"}).status_code == 200


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


def test_photo_task_needs_a_photo_whoever_ticks_it(env):
    tid = "palmetto_2026-11-19_t3"
    env.client.get("/v1/days/2026-11-19", headers=env.crew_h)
    assert env.client.post(f"/v1/tasks/{tid}/complete", json={}, headers=env.crew_h).status_code == 400
    assert env.client.post(f"/v1/tasks/{tid}/complete", json={}, headers=env.boss_h).status_code == 400
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
    posts = []
    monkeypatch.setattr(app_mod.clickup_chat, "post_message", lambda channel, text: posts.append((channel, text)))
    out = env.client.post("/internal/tick", headers=h).get_json()["palmetto"]
    assert out["mirror"] == {"tasks": 3} and out["posted"] == ["palmetto_2026-11-19_opening"]
    assert env.client.post("/internal/tick", headers=h).get_json()["palmetto"]["posted"] == []
    assert [c for c, _ in posts] == ["8cr6661-7217"] and posts[0][1].startswith("**Opening duties · Thu Nov 19**")


def test_shift_summary_counts_shoutouts_open_tasks_and_missing_photos(env):
    tasks, day = env.tasks, datetime.date(2026, 11, 19)
    env.client.get("/v1/days/2026-11-19", headers=env.crew_h)
    tasks.complete("palmetto", "palmetto_2026-11-19_t1", {"sid": env.crew["id"], "role": "crew"})
    tasks.docs.set(st.TASKS, "palmetto_2026-11-19_t2", {"status": "done", "completed_by": env.crew["id"]}, merge=True)
    # A task ticked with tonight's step undone is called out (a Friday step on a ClickUp day is not).
    tasks.docs.set(st.TASKS, "palmetto_2026-11-19_fri", {
        "id": "palmetto_2026-11-19_fri", "store": "palmetto", "date": "2026-11-19", "shift": "opening",
        "title": "Fri: shelf", "parent_id": "palmetto_2026-11-19_t2", "status": "open", "order": 3})
    text = summaries.shift_summary(tasks, "palmetto", day, "opening")
    assert text.splitlines() == [
        "**Opening duties · Thu Nov 19**", "2 of 2 done 🎉", "Shoutouts: Dolce Johnson 2", "Steps not ticked: Thu: blender"]
    # ClickUp never enforced photos: a step done without its photo is called out.
    tasks.docs.set(st.TASKS, "palmetto_2026-11-19_t3", {"status": "done", "completed_by": env.crew["id"]}, merge=True)
    assert summaries.shift_summary(tasks, "palmetto", day, "opening").splitlines()[-1] == (
        "Done without the photo it needs: Thu: blender")


def test_a_clickup_day_waits_for_fresh_clickup_data_before_its_summary(env):
    tasks = env.tasks
    tasks.put_store(dataclasses.replace(tasks.store("palmetto"), cutover_date=None))
    env.clock.at = datetime.datetime(2026, 11, 19, 8, 29, tzinfo=TZ)
    assert summaries.pending(tasks, "palmetto") == []  # the opening crew is still on until 08:00, + 30 min
    env.clock.at = datetime.datetime(2026, 11, 19, 8, 45, tzinfo=TZ)
    assert summaries.wants_mirror(tasks, "palmetto")
    sent = []
    assert summaries.post_due(tasks, "palmetto", lambda c, t: sent.append(t)) == [] and sent == []
    tasks.job_done("palmetto", "mirror")
    assert not summaries.wants_mirror(tasks, "palmetto")
    assert summaries.post_due(tasks, "palmetto", lambda c, t: sent.append(t)) == ["palmetto_2026-11-19_opening"]
    # ClickUp unreachable: after an hour the summary goes out from what is stored.
    env.clock.at = datetime.datetime(2026, 11, 19, 15, 35, tzinfo=TZ)
    assert summaries.wants_mirror(tasks, "palmetto")
    env.clock.at = datetime.datetime(2026, 11, 19, 16, 31, tzinfo=TZ)
    assert summaries.post_due(tasks, "palmetto", lambda c, t: sent.append(t)) == ["palmetto_2026-11-19_shift_change"]


def test_closing_update_follows_the_form_sections_and_posts_again_when_resent(env):
    tasks = env.tasks
    env.clock.at = datetime.datetime(2026, 11, 19, 7, 5, tzinfo=TZ)  # before any shift summary is due
    cfg = tasks.store("palmetto")
    items = ({"id": "b-mango", "name": "Mango", "kind": "text"}, {"id": "f-mango", "name": "Mango", "kind": "text"},
             {"id": FIELD, "name": "Açaí", "kind": "text"}, {"id": "new", "name": "Kiwi", "kind": "text"})
    tasks.put_store(dataclasses.replace(cfg, count_items=items))
    count = {"items": {"b-mango": "12+65%", "f-mango": "8 palettes", FIELD: "25+40%"}}
    update = ("Submitted by: Huy\n\n**Shift notes**\n*   Shift notes: Steady\n\n**BASES**\n*   Blade: 1\n"
              "*   Acai: 25+40%\n*   Mango: 12+65%\n\n**FRUIT**\n*   Mango: 8 palettes\n\n**NEED ATTENTION**\n"
              "*   Manual LOW notes: None")
    placed = summaries.sections_from_update(tasks.store("palmetto"), count, update)
    # Same name, told apart by that day's values; a field the post never showed keeps its place at the end.
    assert [(i["id"], i.get("section")) for i in placed] == [
        (FIELD, "Bases"), ("b-mango", "Bases"), ("f-mango", "Fruit"), ("new", None)]
    tasks.put_store(dataclasses.replace(tasks.store("palmetto"), count_items=placed))
    env.client.put("/v1/counts/2026-11-19", json={"items": count["items"], "notes": {"shift": "Steady", "low": "oat milk"}},
                   headers=env.crew_h)
    sent = []
    send = lambda c, t: sent.append(t)
    assert summaries.post_due(tasks, "palmetto", send)[0].startswith("palmetto_2026-11-19_count_")
    assert "Sent by Dolce Johnson at 7:05 AM" in sent[0] and "**BASES**\n*   Açaí: 25+40%\n*   Mango: 12+65%" in sent[0]
    assert "Running low: oat milk · Out of: None" in sent[0] and "**FRUIT**\n*   Mango: 8 palettes" in sent[0]
    assert summaries.post_due(tasks, "palmetto", send) == []
    env.clock.at += datetime.timedelta(minutes=5)
    env.client.put("/v1/counts/2026-11-19", json={"items": {FIELD: "24"}}, headers=env.crew_h)
    assert len(summaries.post_due(tasks, "palmetto", send)) == 1


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


def _tpl(**kw):
    return model.Template.from_dict({"id": "x", "store": "palmetto", "shift": "opening", "title": "x", **kw})


def test_repeat_rules_decide_which_days_a_task_is_on():
    d = datetime.date
    assert model.due_on(_tpl(), d(2026, 11, 19))
    assert not model.due_on(_tpl(active=False), d(2026, 11, 19))
    assert [model.due_on(_tpl(weekdays=[3]), d(2026, 11, n)) for n in (19, 20)] == [True, False]
    # Every other Thursday, counted from the week it starts.
    other = _tpl(weekdays=[3], every_weeks=2, start_date="2026-11-19")
    assert [model.due_on(other, d(2026, 11, 19) + datetime.timedelta(days=7 * n)) for n in range(4)] == [
        True, False, True, False]
    assert [model.due_on(_tpl(month_days=[1, 15]), d(2026, 12, n)) for n in (1, 2, 15)] == [True, False, True]
    last = _tpl(month_days=[model.LAST_DAY])
    assert model.due_on(last, d(2027, 2, 28)) and not model.due_on(last, d(2027, 2, 27))
    window = _tpl(start_date="2026-11-20", end_date="2026-11-21")
    assert [model.due_on(window, d(2026, 11, n)) for n in (19, 20, 21, 22)] == [False, True, True, False]


def test_manager_adds_a_repeating_task_and_a_schedule_change_updates_days_already_built(env):
    c = env.client
    assert c.post("/v1/templates", headers=env.crew_h, json={"shift": "closing", "title": "x"}).status_code == 403
    bad = c.post("/v1/templates", headers=env.boss_h, json={"shift": "closing", "title": "x", "every_weeks": 99})
    assert bad.status_code == 400
    t = c.post("/v1/templates", headers=env.boss_h, json={
        "shift": "closing", "title": "Filter", "weekdays": [3], "every_weeks": 2}).get_json()["template"]
    assert t["start_date"] == "2026-11-19"  # the weeks are counted from the day it was set up
    titles = lambda day: [x["title"] for x in c.get(f"/v1/days/{day}", headers=env.crew_h).get_json()["tasks"]]
    assert "Filter" in titles("2026-11-19") and "Filter" not in titles("2026-11-26")
    detail = c.get(f"/v1/tasks/palmetto_2026-11-19_{t['id']}", headers=env.crew_h).get_json()
    assert detail["definition"]["every_weeks"] == 2

    # Moved to Fridays in the other shift: today's untouched copy goes, tomorrow's appears there.
    c.post("/v1/templates", headers=env.boss_h, json={**t, "weekdays": [4], "every_weeks": 1, "shift": "opening"})
    assert "Filter" not in titles("2026-11-19")
    fri = c.get("/v1/days/2026-11-20", headers=env.crew_h).get_json()["tasks"]
    assert [x["shift"] for x in fri if x["title"] == "Filter"] == ["opening"]

    # Taking a parent off the list takes its steps with it.
    assert "Thu: blender" in titles("2026-11-19")
    c.post("/v1/templates", headers=env.boss_h, json={"id": "t2", "shift": "opening", "title": "Deep Clean",
                                                      "order": 2, "active": False})
    assert not {"Deep Clean", "Thu: blender"} & set(titles("2026-11-19"))


def test_duplicate_starts_with_the_originals_example_photos(env):
    c = env.client
    c.get("/v1/days/2026-11-19", headers=env.crew_h)
    c.post("/v1/tasks/palmetto_2026-11-19_t1/reference-photo", headers=env.boss_h,
           data={"photo": (io.BytesIO(b"jpg"), "p.jpg", "image/jpeg")})
    copy = c.post("/v1/templates", headers=env.boss_h, json={
        "shift": "closing", "title": "Lock up", "copy_from": "t1"}).get_json()["template"]
    detail = c.get(f"/v1/tasks/palmetto_2026-11-19_{copy['id']}", headers=env.crew_h)
    assert detail.status_code == 404  # not built until the day is read
    c.get("/v1/days/2026-11-19", headers=env.crew_h)
    detail = c.get(f"/v1/tasks/palmetto_2026-11-19_{copy['id']}", headers=env.crew_h).get_json()
    assert len(detail["reference_photos"]) == 1


def test_shift_team_comes_from_shared_schedule(env):
    rows = [{"date": "2026-11-19", "name": "Johnson, Dolce J", "ranges": ["6:30 AM - 2:30 PM"]},
            {"date": "2026-11-19", "name": "Vu, Phong", "ranges": ["3:00 PM - 8:30 PM"]}]
    out = shared_sync.sync(env.tasks, "palmetto", read=lambda ds, a, b: rows, roster=lambda ds, s: [])
    assert out["days"] == shared_sync.DAYS_BACK + shared_sync.DAYS_AHEAD + 1
    teams = env.client.get("/v1/days/2026-11-19", headers=env.crew_h).get_json()["teams"]
    assert teams["opening"] == [{"id": env.crew["id"], "name": "Dolce Johnson"}]
    assert teams["shift_change"] == [{"id": env.crew["id"], "name": "Dolce Johnson"}]
    assert teams["closing"] == [{"id": None, "name": "Vu, Phong"}]


def test_only_people_adp_employs_can_be_picked(env):
    leaver = env.tasks.save_staff("palmetto", {"name": "Old Hand"}, "test")
    nick = env.tasks.save_staff("palmetto", {"name": "Quinnz", "adp_name": "Nguyen, Quyen P"}, "test")
    sync = lambda names: shared_sync.sync(env.tasks, "palmetto", read=lambda ds, a, b: [], roster=lambda ds, s: names)
    out = sync(["Johnson, Dolce J", "Nguyen, Quyen P", "Guerrero, Amy"])
    assert (out["adp_active"], out["added"]) == (3, 1)
    staff = {s["name"]: s for s in env.client.get("/v1/bootstrap", headers=env.ipad).get_json()["staff"]}
    assert {n for n, s in staff.items() if s["assignable"]} == {"Dolce Johnson", "Quinnz", "Amy Guerrero"}
    assert staff["Amy Guerrero"]["adp_name"] == "Guerrero, Amy"
    env.client.get("/v1/days/2026-11-19", headers=env.crew_h)
    one = ["palmetto_2026-11-19_t1"]
    assert env.client.post("/v1/assign", json={"task_ids": one, "assignee_id": leaver["id"]}, headers=env.crew_h).status_code == 400
    assert env.client.post("/v1/assign", json={"task_ids": one, "assignee_id": nick["id"]}, headers=env.crew_h).status_code == 200
    added = env.client.post("/v1/tasks", json={"title": "x", "shift": "opening", "assignee_id": env.boss["id"]}, headers=env.crew_h)
    assert added.status_code == 400  # the manager is not on the ADP roster here
    # A failed scrape (no one active) changes nothing.
    assert sync([])["skipped"] and env.tasks.docs.get(st.STAFF, nick["id"])["adp_active"]


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
            # Recurs under a months-old parent that is on no day's list: not carried over.
            _cu(f"g{day}", "Ghost step", day, parent="old"),
        ]
    raw["opening"] += [_cu("once", "One-off", 10), _cu("old", "Line: Renamed since", 1)]
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
    assert not any(t["title"] == "Ghost step" for t in before["tasks"])
    assert env.client.post("/v1/tasks/cu_p18/complete", json={}, headers=env.crew_h).status_code == 400
    assert verify_backfill.verify(env.tasks, "palmetto", raw)["ok"]
    env.tasks.docs.set(st.TASKS, "cu_a5", {"status": "open"}, merge=True)
    env.tasks.docs.delete(st.TASKS, "cu_a6")
    broken = verify_backfill.verify(env.tasks, "palmetto", raw)
    assert not broken["ok"] and broken["missing"] == ["cu_a6"] and ("cu_a5", "status") in broken["field_mismatches"]


def test_everything_clickup_holds_on_a_task_is_copied_once(env):
    tid = ci.template_id("closing", None, "Upload deep clean photo")
    env.tasks.save_template("palmetto", {"id": tid, "shift": "closing", "title": "Upload deep clean photo"}, "test")
    env.tasks.docs.set(st.TASKS, "cu_x1", {
        "id": "cu_x1", "store": "palmetto", "date": "2026-11-10", "shift": "closing", "title": "Upload deep clean photo",
        "parent_id": None, "status": "done", "source": "clickup", "clickup_id": "x1", "photos": [], "comments": []})
    calls = []
    when = "1789322744505"
    page1 = [{"id": str(i), "user": {"id": 7}, "date": when, "comment_text": f"note {i}", "reply_count": 0}
             for i in range(ci.COMMENT_PAGE)]
    page1[0].update(reply_count=1, comment=[{"text": "see"}, {"image": {"id": "img", "url": "https://files/c1"}}])

    def get(path):
        calls.append(path)
        if path.startswith("/comment/"):
            return {"comments": [{"id": "r1", "user": {"id": 8}, "date": when, "comment_text": "thanks"}]}
        if "/comment" in path:
            return {"comments": page1 if "start=" not in path else [
                {"id": "old", "user": {"id": 7}, "date": when, "comment_text": "oldest"}]}
        return {"tags": [{"name": "deep clean"}], "priority": {"priority": "high"}, "custom_fields": [],
                "checklists": [{"name": "Steps", "items": [{"name": "fridge", "resolved": True}]}, {"items": []}],
                "attachments": [
                    {"id": "a1.jpg", "mimetype": "image/jpeg", "url": "https://files/a1", "date": when, "user": {"id": 7}},
                    {"id": "a2.mp4", "mimetype": "video/mp4", "title": "walkthrough.mp4", "url": "https://files/a2", "date": when}]}

    files = {"https://files/a1": b"\xff\xd8a1", "https://files/a2": b"mp4", "https://files/c1": b"\xff\xd8c1"}
    report = ci.import_task_files(env.tasks, env.tasks.store("palmetto"), get=get,
                                  download=lambda url: (files[url], "application/octet-stream"), workers=1)
    assert (report["photos"], report["files"], report["comments"], report["checklists"]) == (2, 1, 27, 1)
    doc = env.tasks.docs.get(st.TASKS, "cu_x1")
    assert doc["files"][0]["name"] == "walkthrough.mp4" and env.tasks.photos.get(doc["files"][0]["path"])[0] == b"mp4"
    assert any(p.get("comment_id") == "cu0" for p in doc["photos"])
    assert {"oldest", "thanks"} <= {c["text"] for c in doc["comments"]}
    assert next(c for c in doc["comments"] if c["text"] == "thanks")["reply_to"] == "cu0"
    assert doc["checklists"] == [{"name": "Steps", "items": [{"name": "fridge", "done": True}]}]
    assert doc["clickup"]["tags"] == ["deep clean"] and doc["clickup"]["priority"] == "high"
    raw = json.loads(env.tasks.photos.get(ci.archive_path("palmetto", "tasks", "x1.json"))[0])
    assert raw["task"]["priority"]["priority"] == "high" and len(raw["comments"]) == ci.COMMENT_PAGE + 1
    calls.clear()
    assert ci.import_task_files(env.tasks, env.tasks.store("palmetto"), get=get, workers=1)["tasks"] == 0 and not calls
    assert ci.seed_recent_photos(env.tasks, env.tasks.store("palmetto")) == 1
    assert env.tasks.docs.get(st.TEMPLATES, tid)["recent_photos"][0]["date"] == "2026-11-10"



def test_mirror_keeps_copying_status_comments_and_photos_of_changed_tasks(env):
    cfg = env.tasks.store("palmetto")
    env.tasks.put_store(dataclasses.replace(cfg, cutover_date=None))  # ClickUp still runs the store
    env.clock.at = datetime.datetime(2026, 11, 19, 9, 0, tzinfo=TZ)
    task = {**_cu("live", "Wipe tables", 19, st="closed"), "date_updated": _ms(2026, 11, 19, 8)}
    lists = list(env.tasks.store("palmetto").clickup["lists"])

    def get(path):
        if path.startswith(f"/list/{lists[0]}/task"):
            return {"tasks": [task], "last_page": True}
        if path.startswith("/list/"):
            return {"tasks": [], "last_page": True}
        if path == "/team":
            return {"teams": [{"id": cfg.clickup["team_id"], "members": []}]}
        if path.endswith("/comment"):
            return {"comments": [{"id": "c9", "user": {"id": 7}, "date": _ms(2026, 11, 19, 8), "comment_text": "done!"}]}
        return {"attachments": [{"id": "p9.jpg", "mimetype": "image/jpeg", "url": "https://files/p9",
                                 "date": _ms(2026, 11, 19, 8), "user": {"id": 7}}]}

    first = ci.mirror(env.tasks, "palmetto", get=get, download=lambda u: (b"\xff\xd8", "image/jpeg"))
    doc = env.tasks.docs.get(st.TASKS, "cu_live")
    assert first["files"]["photos"] == 1 and doc["status"] == "done" and doc["comments"][0]["text"] == "done!"
    # Nothing changed in ClickUp since: the next run copies no files again.
    env.clock.at += datetime.timedelta(hours=2)
    assert "files" not in ci.mirror(env.tasks, "palmetto", get=get, download=lambda u: 1 / 0)
    # A new comment bumps ClickUp's update time: the task is copied again, without doubling the photo.
    task["date_updated"] = _ms(2026, 11, 19, 12)
    again = ci.mirror(env.tasks, "palmetto", get=get, download=lambda u: 1 / 0)
    assert again["files"]["tasks"] == 1 and len(env.tasks.docs.get(st.TASKS, "cu_live")["photos"]) == 1


def test_photo_rules_come_from_what_clickup_holds(env):
    def tpl(title, **kw):
        tid = ci.template_id("closing", kw.pop("parent", None), title)
        env.tasks.save_template("palmetto", {"id": tid, "shift": "closing", "title": title, **kw}, "test")
        return tid
    deep = tpl("Deep clean")
    tpl("Tue: freezer", parent="Deep clean", parent_id=deep)
    tpl("Acai thaw", instructions="Weekdays: 2 - ATTACH PHOTO")
    tpl("Stow deliveries - confirm match w/ photo of invoice")
    tpl("Wipe sink")
    for i, (title, parent) in enumerate([("Tue: freezer", "Deep clean")] * 3 + [("Deep clean", None)] * 3 + [("Wipe sink", None)] * 2):
        env.tasks.docs.set(st.TASKS, f"cu_p{i}", {
            "id": f"cu_p{i}", "store": "palmetto", "date": f"2026-10-{i + 1:02d}", "shift": "closing", "title": title,
            "parent_id": "cu_p3" if parent else None, "source": "clickup", "photos": [{"path": "x"}]})
    rules = {r["title"]: r for r in ci.photo_rules(env.tasks, "palmetto")}
    # Steps carry the rule, not the task that holds them; a passing mention of a photo is not a request.
    assert set(rules) == {"Tue: freezer", "Acai thaw"}
    assert rules["Tue: freezer"]["photo_days"] == 3 and rules["Acai thaw"]["asks"]
