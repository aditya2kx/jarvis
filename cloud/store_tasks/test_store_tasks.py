"""Tasks API: access rules, daily lists, ticks, counts, alerts, ClickUp import, shared shifts."""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import hmac
import io
import json
import pathlib

import pytest
from PIL import Image

from skills.google_signin import identity

from agents.bhaga.scripts.ingest_inventory import rows_from_task
from cloud.store_tasks import admin, app as app_mod
from cloud.store_tasks import auth, clickup_import as ci, clickup_webhooks as cw, events as ev, model, service as svc
from cloud.store_tasks import notify, shared_sync, store as st, summaries, tracker as tr, verify_backfill

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


def test_google_sign_in_hop_hands_the_one_link_a_device_token(env, monkeypatch):
    monkeypatch.setenv("STORE_TASKS_HOME_URL", "https://tasks.example/")
    c = env.client
    r = c.get("/v1/handoff", headers={"X-Test-Google": OWNER})
    assert r.status_code == 302 and r.location.startswith("https://tasks.example/#signin=")
    token = r.location.split("#signin=")[1]
    boot = c.get("/v1/bootstrap", headers={"X-Shop-Device": token}).get_json()
    assert boot["account"]["email"] == OWNER and not boot["account"]["shop"]
    assert boot["own"]["staff"]["id"] == env.boss["id"]  # a person's own account: no PIN
    assert boot["device"]  # renewed on every bootstrap
    h = {"X-Shop-Device": token, "X-Staff-Session": boot["own"]["session"]}
    assert c.get("/v1/templates", headers=h).status_code == 200
    # A shared account signed in through the hop still needs a PIN.
    ipad = c.get("/v1/handoff", headers=env.ipad).location.split("#signin=")[1]
    assert c.get("/v1/bootstrap", headers={"X-Shop-Device": ipad}).get_json()["own"] is None
    # Not on the list, or not signed in: back to the link with the reason, never a token.
    r = c.get("/v1/handoff", headers={"X-Test-Google": "stranger@gmail.com"})
    assert "#signin_error=" in r.location and "#signin=" not in r.location
    assert "#signin_error=" in c.get("/v1/handoff").location
    # Removing the account stops its token at the next call.
    env.tasks.remove_account("palmetto", OWNER)
    assert c.get("/v1/bootstrap", headers={"X-Shop-Device": token}).status_code == 403


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


def test_clickup_days_can_be_tried_out_and_the_sync_puts_them_back(env):
    cfg = dataclasses.replace(env.tasks.store("palmetto"), cutover_date=None)
    env.tasks.put_store(cfg)
    tid = ci.template_id("closing", None, "Wipe the door")
    env.tasks.save_template("palmetto", {"id": tid, "shift": "closing", "title": "Wipe the door", "needs_photo": True}, "test")
    kept = {"path": "palmetto/2026-11-19/cu_d/cu1", "by": "cu7", "at": "2026-11-19T08:00:00-06:00",
            "clickup_attachment_id": "cu1"}
    for day in ("2026-11-18", "2026-11-19"):
        docs = [ci.to_task(cfg, "closing", {"id": f"d{day[-2:]}", "name": "Wipe the door", "status": {"type": "open"},
                                             "start_date": None, "due_date": None, "date_created": "1795600800000"},
                           datetime.date.fromisoformat(day))]
        ci._reparent(docs)
        ci.write_tasks(env.tasks, cfg, docs)
    task = "cu_d19"
    env.tasks.docs.set(st.TASKS, task, {"photos": [kept], "comments": [{"id": "cu9", "text": "from ClickUp", "at": "x"}]}, merge=True)
    c, h = env.client, env.boss_h

    shown = next(t for t in c.get("/v1/days/2026-11-19", headers=h).get_json()["tasks"] if t["id"] == task)
    assert shown["template_id"] == tid and shown["needs_photo"]  # our photo rule shows on ClickUp's task
    assert c.post("/v1/tasks/cu_d18/complete", json={}, headers=h).status_code == 400  # history stays ClickUp's
    assert _upload(env, f"/v1/tasks/{task}/photo", h).status_code == 200
    assert c.post(f"/v1/tasks/{task}/complete", json={}, headers=h).get_json()["status"] == "done"
    assert c.post("/v1/assign", json={"task_ids": [task], "assignee_id": env.crew["id"]}, headers=h).status_code == 200
    c.post(f"/v1/tasks/{task}/comments", json={"text": "trying it"}, headers=h)
    assert c.delete(f"/v1/tasks/{task}/photos/{kept['path']}", headers=h).status_code == 400  # ClickUp's photo

    # Summaries and alerts see ClickUp's state, never the try-out.
    seen = svc.clickup_view(env.tasks.docs.get(st.TASKS, task))
    assert seen["status"] == "open" and seen["assignee_id"] is None and seen["photos"] == [kept]
    assert [c["id"] for c in seen["comments"]] == ["cu9"]
    assert "0 of" in summaries.shift_summary(env.tasks, "palmetto", datetime.date(2026, 11, 19), "closing")
    assert env.tasks.docs.get(st.TEMPLATES, tid).get("recent_photos") is None  # a try-out photo is not history

    assert env.tasks.reset_trials("palmetto") == 0  # today: still trying it out
    env.clock.at += datetime.timedelta(days=1)
    assert env.tasks.reset_trials("palmetto") == 1
    back = env.tasks.docs.get(st.TASKS, task)
    assert back["status"] == "open" and back["assignee_id"] is None and back["photos"] == [kept]
    assert "trial" not in back and "clickup_state" not in back and len(back["comments"]) == 1


def test_a_clickup_change_ends_a_try_out_tick(env):
    cfg = dataclasses.replace(env.tasks.store("palmetto"), cutover_date=None)
    env.tasks.put_store(cfg)
    raw = {"id": "e1", "name": "Mop", "status": {"type": "open"}, "start_date": None, "due_date": None,
           "date_created": "1795600800000"}
    ci.write_tasks(env.tasks, cfg, [ci.to_task(cfg, "closing", raw, datetime.date(2026, 11, 19))])
    env.client.post("/v1/tasks/cu_e1/complete", json={}, headers=env.boss_h)
    done_in_clickup = {**raw, "status": {"type": "closed"}, "date_closed": "1795611600000", "assignees": [{"id": 5}]}
    ci.write_tasks(env.tasks, cfg, [ci.to_task(cfg, "closing", done_in_clickup, datetime.date(2026, 11, 19))])
    seen = svc.clickup_view(env.tasks.docs.get(st.TASKS, "cu_e1"))
    assert seen["status"] == "done" and seen["completed_by"] == "cu5"
    # A later try-out keeps ClickUp's new state to go back to.
    env.client.post("/v1/tasks/cu_e1/reopen", headers=env.boss_h)
    env.clock.at += datetime.timedelta(days=1)
    env.tasks.reset_trials("palmetto")
    assert env.tasks.docs.get(st.TASKS, "cu_e1")["completed_by"] == "cu5"


def test_editing_a_definition_never_touches_clickup_copies(env):
    cfg = dataclasses.replace(env.tasks.store("palmetto"), cutover_date=None)
    env.tasks.put_store(cfg)
    tid = ci.template_id("closing", None, "Mop")
    env.tasks.save_template("palmetto", {"id": tid, "shift": "closing", "title": "Mop"}, "test")
    docs = [ci.to_task(cfg, "closing", {"id": "m1", "name": "Mop", "status": {"type": "open"}, "start_date": None,
                                        "due_date": None, "date_created": "1795600800000"}, datetime.date(2026, 11, 20))]
    ci._reparent(docs)
    ci.write_tasks(env.tasks, cfg, docs)
    env.tasks.save_template("palmetto", {"id": tid, "shift": "closing", "title": "Mop well", "weekdays": [0]}, "test")
    assert env.tasks.docs.get(st.TASKS, "cu_m1")["title"] == "Mop"


def test_a_clickup_task_shows_its_definitions_how_to(env):
    cfg = dataclasses.replace(env.tasks.store("palmetto"), cutover_date=None)
    env.tasks.put_store(cfg)
    tid = ci.template_id("closing", None, "Mop")
    env.tasks.save_template("palmetto", {"id": tid, "shift": "closing", "title": "Mop"}, "test")
    docs = [ci.to_task(cfg, "closing", {"id": "m1", "name": "Mop", "status": {"type": "open"}, "start_date": None,
                                        "due_date": None, "date_created": "1795600800000",
                                        "text_content": "ClickUp's words"}, datetime.date(2026, 11, 19))]
    ci._reparent(docs)
    ci.write_tasks(env.tasks, cfg, docs)
    c, h = env.client, env.boss_h

    def listed():
        return next(t for t in c.get("/v1/days/2026-11-19", headers=h).get_json()["tasks"] if t["id"] == "cu_m1")
    assert listed()["instructions"] == "ClickUp's words"  # no how-to of ours yet: ClickUp's text
    r = c.post("/v1/tasks/cu_m1/instructions", json={"instructions": "1. Wet floor sign out."}, headers=h)
    assert r.get_json()["instructions"] == "1. Wet floor sign out."
    assert listed()["instructions"] == "1. Wet floor sign out."
    assert c.get("/v1/tasks/cu_m1", headers=h).get_json()["task"]["instructions"] == "1. Wet floor sign out."
    assert c.post("/v1/tasks/cu_m1/complete", json={}, headers=h).get_json()["instructions"] == "1. Wet floor sign out."
    assert env.tasks.docs.get(st.TASKS, "cu_m1")["instructions"] == "ClickUp's words"  # ClickUp's copy untouched


def test_feedback_keeps_where_they_were_and_what_they_did_and_tells_the_operator(env, monkeypatch):
    sent = []
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("STORE_TASKS_FEEDBACK_SLACK_USER", "U1")
    monkeypatch.setattr(notify, "_slack", lambda token, method, payload: sent.append((method, payload)) or {"channel": {"id": "D1"}})
    c = env.client
    context = {"screen": "Day", "date": "2026-11-19", "shift": "closing", "task": "Mop", "task_id": "x", "online": True}
    events = [{"at": f"t{i}", "kind": "api", "what": "x" * 200} for i in range(150)]
    r = c.post("/v1/feedback", json={"kind": "problem", "text": "Tick did nothing", "context": context, "events": events},
               headers=env.crew_h)
    fid = r.get_json()["id"]
    assert c.post("/v1/feedback", json={"text": "  "}, headers=env.crew_h).status_code == 400
    photo = (io.BytesIO(JPEG), "shot.jpg", "image/jpeg")
    c.post("/v1/feedback", data={"data": json.dumps({"kind": "idea", "text": ""}), "photo": photo},
           headers=env.crew_h, content_type="multipart/form-data")

    assert c.get("/v1/feedback", headers=env.crew_h).status_code == 403
    listed = c.get("/v1/feedback", headers=env.boss_h).get_json()["feedback"]
    assert sorted(f["kind"] for f in listed) == ["idea", "problem"]
    first = next(f for f in listed if f["id"] == fid)
    assert first["staff_name"] == "Dolce Johnson" and first["context"]["task"] == "Mop"
    assert 0 < len(first["events"]) <= svc.MAX_FEEDBACK_EVENTS and first["events"][-1]["at"] == "t149"
    assert next(f for f in listed if f["kind"] == "idea")["photos"]
    assert c.post(f"/v1/feedback/{fid}/done", json={}, headers=env.boss_h).get_json()["status"] == "done"

    posts = [p for m, p in sent if m == "chat.postMessage"]
    assert len(posts) == 2 and posts[0]["channel"] == "D1"
    assert "Tick did nothing" in posts[0]["text"] and "Closing list" in posts[0]["text"] and fid in posts[0]["text"]


def test_a_manager_puts_a_shift_in_the_order_it_is_done(env):
    c, h = env.client, env.boss_h
    assert c.post("/v1/templates/order", json={"shift": "opening", "ids": ["t2"]}, headers=env.crew_h).status_code == 403
    assert c.post("/v1/templates/order", json={"shift": "opening", "ids": ["t5"]}, headers=h).status_code == 400
    assert c.post("/v1/templates/order", json={"shift": "opening", "ids": ["t2"]}, headers=h).get_json() == {"changed": 2}
    assert c.post("/v1/templates/order", json={"shift": "opening", "ids": ["t2"]}, headers=h).get_json() == {"changed": 0}
    top = [t["title"] for t in c.get("/v1/days/2026-11-19", headers=h).get_json()["tasks"]
           if t["shift"] == "opening" and not t["parent_id"]]
    assert top == ["Deep Clean", "Unlock"]
    assert len(env.tasks.docs.query(st.TEMPLATE_HISTORY, template_id="t1")) == 1  # the old order is kept

    env.tasks.put_store(dataclasses.replace(env.tasks.store("palmetto"), cutover_date=None))
    base = {"store": "palmetto", "date": "2026-11-19", "shift": "opening", "source": "clickup", "status": "open",
            "parent_id": None}
    env.tasks.docs.set(st.TASKS, "cu_u", {**base, "id": "cu_u", "title": "Unlock", "template_id": "t1", "order": 1.0})
    env.tasks.docs.set(st.TASKS, "cu_d", {**base, "id": "cu_d", "title": "Deep Clean", "template_id": "t2", "order": 2.0})
    shown = [t["id"] for t in c.get("/v1/days/2026-11-19", headers=h).get_json()["tasks"] if t["shift"] == "opening"]
    assert shown == ["cu_d", "cu_u"]  # ClickUp's tasks in our order
    assert env.tasks.docs.get(st.TASKS, "cu_u")["order"] == 1.0  # ClickUp's copy untouched


def test_days_ahead_show_the_tasks_clickup_has_not_added_yet(env):
    env.tasks.put_store(dataclasses.replace(env.tasks.store("palmetto"), cutover_date=None))
    base = {"store": "palmetto", "date": "2026-11-20", "shift": "opening", "source": "clickup", "status": "open"}
    env.tasks.docs.set(st.TASKS, "cu_u", {**base, "id": "cu_u", "title": "Unlock", "template_id": "t1", "parent_id": None})
    env.tasks.docs.set(st.TASKS, "cu_dc", {**base, "id": "cu_dc", "title": "deep  clean", "parent_id": None})
    c, h = env.client, env.crew_h

    tasks = c.get("/v1/days/2026-11-20", headers=h).get_json()["tasks"]
    assert {t["title"]: (t["source"], t["parent_id"]) for t in tasks} == {
        "Unlock": ("clickup", None), "deep  clean": ("clickup", None),
        "Fri: shelf": ("planned", "cu_dc"), "Mop": ("planned", None)}
    mop = next(t for t in tasks if t["title"] == "Mop")
    assert c.get(f"/v1/tasks/{mop['id']}", headers=h).get_json()["task"]["title"] == "Mop"
    assert c.post(f"/v1/tasks/{mop['id']}/complete", json={}, headers=h).status_code == 404
    assert env.tasks.docs.get(st.TASKS, mop["id"]) is None  # a preview, never stored
    today = c.get("/v1/days/2026-11-19", headers=h).get_json()["tasks"]
    assert not any(t["source"] == "planned" for t in today)  # today is exactly what ClickUp has


def test_link_templates_gives_clickup_tasks_their_definition_and_earlier_photos(env):
    cfg = env.tasks.store("palmetto")
    tid = ci.template_id("closing", "Deep clean", "Fridge")
    env.tasks.save_template("palmetto", {"id": tid, "shift": "closing", "title": "Fridge"}, "test")
    env.tasks.docs.set(st.TASKS, "cu_p", {"id": "cu_p", "store": "palmetto", "date": "2026-11-10", "shift": "closing",
                                          "title": "Deep clean", "parent_id": None, "source": "clickup"})
    env.tasks.docs.set(st.TASKS, "cu_s", {"id": "cu_s", "store": "palmetto", "date": "2026-11-10", "shift": "closing",
                                          "title": "Fridge", "parent_id": "cu_p", "source": "clickup",
                                          "photos": [{"path": "palmetto/2026-11-10/cu_s/a", "clickup_attachment_id": "a"}]})
    assert ci.link_templates(env.tasks, cfg) == 2
    assert ci.link_templates(env.tasks, cfg) == 0
    assert env.tasks.docs.get(st.TASKS, "cu_s")["template_id"] == tid
    ci.seed_recent_photos(env.tasks, cfg)
    later = env.tasks.add_one_off("palmetto", {"title": "Fridge", "date": "2026-11-19", "shift": "closing"}, env.boss["id"])
    env.tasks.docs.set(st.TASKS, later["id"], {"template_id": tid}, merge=True)
    detail = env.client.get(f"/v1/tasks/{later['id']}", headers=env.crew_h).get_json()
    assert [p["date"] for p in detail["recent_photos"]] == ["2026-11-10"]


def test_example_media_takes_a_short_video(env):
    tid = "palmetto_2026-11-19_t1"
    env.client.get("/v1/days/2026-11-19", headers=env.crew_h)
    r = env.client.post(f"/v1/tasks/{tid}/reference-photo", headers=env.boss_h,
                        data={"photo": (io.BytesIO(b"\x00\x00\x00\x18ftypmp42"), "how.mp4", "video/mp4")})
    assert r.status_code == 200 and r.get_json()["reference_photos"][0]["type"] == "video/mp4"
    listed = {t["id"]: t for t in env.client.get("/v1/days/2026-11-19", headers=env.crew_h).get_json()["tasks"]}
    assert listed[tid]["examples"] == {"photos": 0, "videos": 1}
    assert listed["palmetto_2026-11-19_t5"]["examples"] == {"photos": 0, "videos": 0}
    r = env.client.post(f"/v1/tasks/{tid}/photo", headers=env.boss_h,
                        data={"photo": (io.BytesIO(b"\x00\x00\x00\x18ftypmp42"), "how.mp4", "video/mp4")})
    assert r.status_code == 400  # a task's own proof is a photo


def test_big_photos_are_served_small(env):
    tid = "palmetto_2026-11-19_t1"
    env.client.get("/v1/days/2026-11-19", headers=env.crew_h)
    big = io.BytesIO()
    Image.effect_noise((2400, 1600), 64).convert("RGB").save(big, "JPEG", quality=95)
    up = env.client.post(f"/v1/tasks/{tid}/photo", headers=env.crew_h,
                         data={"photo": (io.BytesIO(big.getvalue()), "p.jpg", "image/jpeg")})
    path = up.get_json()["photos"][0]["path"]
    for size, edge in svc.PHOTO_SIZES.items():
        r = env.client.get(f"/v1/photos/{path}?size={size}", headers=env.crew_h)
        assert r.status_code == 200 and max(Image.open(io.BytesIO(r.data)).size) == edge
        assert env.tasks.photos.get(f"palmetto/{svc.SIZED_DIR}/{size}/{path.split('/', 1)[1]}")  # kept
    assert env.client.get(f"/v1/photos/{path}", headers=env.crew_h).data == big.getvalue()
    assert env.client.get(f"/v1/photos/{path}?size=huge", headers=env.crew_h).status_code == 400
    small = env.client.get(f"/v1/photos/{path}?size=thumb", headers=env.crew_h).data
    assert len(small) < len(big.getvalue()) / 4


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


def test_a_count_tried_on_a_clickup_day_gives_way_to_the_clickup_count(env):
    day = "2026-11-18"  # before cutover: ClickUp runs it
    _upload(env, f"/v1/counts/{day}/photo", env.crew_h)
    sent = env.client.put(f"/v1/counts/{day}", json={"items": {FIELD: "5"}}, headers=env.crew_h).get_json()["count"]
    assert sent["source"] == svc.COUNT_TRYOUT
    assert env.client.get(f"/v1/days/{day}", headers=env.crew_h).get_json()["count_submitted"] is True
    out = env.client.get("/internal/stores/palmetto/closing-counts",
                         headers={"Authorization": f"Bearer {INTERNAL}"}).get_json()["tasks"]
    assert out == []
    real = {"id": f"palmetto_{day}", "store": "palmetto", "date": day, "items": {FIELD: "40"},
            "source": "clickup", "clickup_id": "cu1", "submitted_at": f"{day}T21:00:00-06:00"}
    assert ci.write_counts(env.tasks, [real]) == 1
    kept = env.tasks.count("palmetto", datetime.date(2026, 11, 18))
    assert kept["items"] == {FIELD: "40"} and kept["photos"] == []


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
    monkeypatch.setattr(cw, "ensure", lambda tasks, sid, root: {"root": root})
    monkeypatch.setattr(cw, "drain", lambda tasks, sid: {})
    posts = []
    monkeypatch.setattr(app_mod.clickup_chat, "post_message", lambda channel, text: posts.append((channel, text)))
    out = env.client.post("/internal/tick", headers=h).get_json()["palmetto"]
    assert out["mirror"] == {"tasks": 3} and out["posted"] == ["palmetto_2026-11-19_opening"]
    assert out["webhooks"] == {"root": "https://localhost/"}
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


def test_mirror_copies_the_photos_attached_to_a_closing_submission(env):
    cfg = env.tasks.store("palmetto")
    env.tasks.put_store(dataclasses.replace(cfg, cutover_date=None))
    env.clock.at = datetime.datetime(2026, 11, 19, 21, 0, tzinfo=TZ)
    lists = list(cfg.clickup["lists"])
    form = {**_cu("f1", "Form Submission - #2026-11-19T19:06:00-06:00", 19), "date_created": _ms(2026, 11, 19, 19, 6),
            "custom_fields": [{"id": FIELD, "name": "Açaí", "type": "short_text", "value": "25+40%"}]}
    fetched = []

    def get(path):
        if path.startswith(f"/list/{lists[-1]}/task"):
            return {"tasks": [form], "last_page": True}
        if path.startswith("/list/"):
            return {"tasks": [], "last_page": True}
        if path == "/team":
            return {"teams": [{"id": cfg.clickup["team_id"], "members": []}]}
        fetched.append(path)  # the form's photos are only on the task itself
        return {"attachments": [{"id": "freezer.jpg", "url": "https://files/freezer", "date": _ms(2026, 11, 19, 19, 6)}]}

    assert ci.mirror(env.tasks, "palmetto", get=get, download=lambda u: (b"\xff\xd8f", "image/jpeg"))["count_photos"] == 1
    photos = env.tasks.count("palmetto", datetime.date(2026, 11, 19))["photos"]
    assert [p["clickup_attachment_id"] for p in photos] == ["freezer.jpg"]
    assert env.tasks.photos.get(photos[0]["path"])[0] == b"\xff\xd8f"
    # Unchanged in ClickUp: not fetched again, and the photo stays.
    fetched.clear()
    assert ci.mirror(env.tasks, "palmetto", get=get, download=lambda u: 1 / 0)["count_photos"] == 0 and not fetched
    assert len(env.tasks.count("palmetto", datetime.date(2026, 11, 19))["photos"]) == 1


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


def test_clickup_webhooks_register_themselves_and_heal(env):
    cfg = env.tasks.store("palmetto")
    lists = list(cfg.clickup["lists"])
    live, calls = {}, []

    def get(path):
        return {"webhooks": list(live.values())}

    def send(method, path, body=None):
        calls.append((method, path))
        if method == "DELETE":
            live.pop(path.rsplit("/", 1)[1])
            return {}
        wid = f"w{len(calls)}"
        live[wid] = {"id": wid, "endpoint": body["endpoint"], "health": {"status": "active"}}
        return {"id": wid, "webhook": {"id": wid, "secret": f"s-{wid}"}}

    assert cw.ensure(env.tasks, "palmetto", "https://api.example/", get, send) == {"kept": 0, "created": 3}
    assert cw.hook(env.tasks, "palmetto", lists[0])["secret"] == "s-w1"
    assert live["w1"]["endpoint"] == f"https://api.example/webhooks/clickup/palmetto/{lists[0]}"
    assert cw.ensure(env.tasks, "palmetto", "https://api.example/", get, send) == {"kept": 3, "created": 0}
    # ClickUp suspends a webhook after failed calls: it is replaced, not left dead.
    live["w2"]["health"]["status"] = "suspended"
    assert cw.ensure(env.tasks, "palmetto", "https://api.example/", get, send) == {"kept": 2, "created": 1}
    assert ("DELETE", "/webhook/w2") in calls and "w2" not in live


def test_a_clickup_change_lands_in_seconds_and_its_files_on_the_next_tick(env, monkeypatch):
    cfg = env.tasks.store("palmetto")
    env.tasks.put_store(dataclasses.replace(cfg, cutover_date=None))
    lists = list(cfg.clickup["lists"])
    env.tasks.docs.set(st.WEBHOOKS, "palmetto", {"id": "palmetto", "hooks": {lists[0]: {"id": "w1", "secret": "shh"}}})
    task = {**_cu("live", "Wipe tables", 19, st="closed"), "list": {"id": lists[0]},
            "attachments": [{"id": "p9.jpg", "mimetype": "image/jpeg", "url": "https://files/p9",
                             "date": _ms(2026, 11, 19, 8), "user": {"id": 7}}]}
    gets = []

    def get(path):
        gets.append(path)
        if path.endswith("/comment"):
            return {"comments": []}
        return task

    body = json.dumps({"event": "taskStatusUpdated", "task_id": "live", "webhook_id": "w1"}).encode()
    url = f"/webhooks/clickup/palmetto/{lists[0]}"
    assert env.client.post(url, data=body, headers={"X-Signature": "forged"}).status_code == 401
    assert env.client.post(f"/webhooks/clickup/palmetto/{lists[1]}", data=body,
                           headers={"X-Signature": "x"}).status_code == 401  # no webhook on that list
    sig = hmac.new(b"shh", body, hashlib.sha256).hexdigest()
    monkeypatch.setattr(ci, "_get", get)
    r = env.client.post(url, data=body, headers={"X-Signature": sig, "Content-Type": "application/json"})
    assert r.get_json() == {"task": "cu_live", "queued": True}
    assert env.tasks.docs.get(st.TASKS, "cu_live")["status"] == "done"  # before any tick
    out = cw.drain(env.tasks, "palmetto", get=get, download=lambda u: (b"\xff\xd8", "image/jpeg"))
    assert out["done"] == 1 and out["files"]["photos"] == 1 and env.tasks.docs.query(st.INBOX) == []
    # A closing form sent in ClickUp becomes the day's count straight away.
    form = {**_cu("f1", "Form Submission - #2026-11-19T19:06", 19), "list": {"id": lists[0]}, "creator": {"id": 7},
            "fields": [], "custom_fields": [{"id": FIELD, "name": "Açaí", "type": "short_text", "value": "25+40%"},
                                            {"id": "f-notes", "name": "Shift notes", "value": "Steady"}]}
    cw.receive(env.tasks, "palmetto", lists[0], {"event": "taskCreated", "task_id": "f1"}, get=lambda p: form)
    c = env.tasks.count("palmetto", datetime.date(2026, 11, 19))
    assert c["items"] == {FIELD: "25+40%"} and c["notes"]["shift"] == "Steady" and c["submitted_by"] == "cu7"
    cw.receive(env.tasks, "palmetto", lists[0], {"event": "taskDeleted", "task_id": "live"})
    assert env.tasks.docs.get(st.TASKS, "cu_live") is None


def test_paced_calls_keep_under_the_budget(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(ci.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(ci.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    get = ci.paced(lambda p: p, per_minute=30)
    assert [get(str(i)) for i in range(4)] == ["0", "1", "2", "3"]
    assert clock["t"] == 6.0  # 2 s apart: the 4th call starts at 6 s


NOTES = """Action items for Thursday. Please post here as you finish them.

[@Lee Manager](#user_mention#77)
1. \\[Houston + Austin\\] Branding material for events, with [@Sam Other](#user_mention#99)
2. Order QR codes

Dolce \\+ Lee
1. Put up laminations
    *   Sink and filter
    *   Frozen delivery
        *   Emailed already
2. 100% shift coverage
Call the landlord
"""


LEE, SAM = "lee@gmail.example", "sam@gmail.example"


def _project(env):
    """Lee runs it; the crew member Dolce (who uses the store iPad) is on it by her own Google account."""
    tr.Tracker(env.tasks).create("leads", "Leadership", [
        {"id": "cu77", "name": "Lee Manager", "emails": [LEE]},
        {"id": "m2", "name": "Dolce Johnson", "emails": [SAM]},
    ], ["cu77"], ["Austin", "Houston"], "test")
    return {"X-Test-Google": LEE}, {"X-Test-Google": SAM}


def test_pasted_meeting_notes_become_items_per_owner(env):
    lee, _ = _project(env)
    drafts = env.client.post("/v1/projects/leads/parse", json={"text": NOTES}, headers=lee).get_json()["items"]
    assert [(d["title"], d["owners"], d["tags"]) for d in drafts] == [
        ("Branding material for events, with Sam Other", ["cu77"], ["Austin", "Houston"]),
        ("Order QR codes", ["cu77"], []),
        ("Put up laminations", ["m2", "cu77"], []),
        ("100% shift coverage", ["m2", "cu77"], []),
        ("Call the landlord", ["m2", "cu77"], []),
    ]
    assert drafts[2]["detail"] == "- Sink and filter\n- Frozen delivery\n  - Emailed already"
    plain = env.client.post("/v1/projects/leads/parse", json={"text": "Buy an iPad\nCall the landlord"}, headers=lee)
    assert [(d["title"], d["owners"]) for d in plain.get_json()["items"]] == [
        ("Buy an iPad", ["cu77"]), ("Call the landlord", ["cu77"])]


def test_a_project_is_apart_from_the_store_and_seen_only_by_its_members(env):
    lee, sam = _project(env)
    c = env.client
    # A member signs in with Google without any store access; store routes still refuse them.
    token = env.tasks.google_device(SAM)
    dev = {"X-Shop-Device": token}
    assert c.get("/v1/bootstrap", headers=dev).status_code == 403
    assert c.get("/v1/projects", headers=dev).get_json() == {"store": None, "stores": [], "projects": [{"id": "leads", "name": "Leadership"}]}
    tr.Tracker(env.tasks).create("owners", "Owners", [{"id": "o1", "name": "Lee", "emails": [OWNER]}], ["o1"], [], "test")
    both = c.get("/v1/projects", headers={"X-Test-Google": OWNER}).get_json()
    assert (both["store"]["id"], both["projects"]) == ("palmetto", [{"id": "owners", "name": "Owners"}])
    with pytest.raises(auth.AuthError):
        env.tasks.google_device("stranger@gmail.example")
    # The store's people, its manager, and the shop device see nothing of it.
    assert c.get("/v1/projects/leads", headers=env.boss_h).status_code == 404
    assert c.get("/v1/projects", headers=env.ipad).get_json()["projects"] == []
    shop = {"X-Shop-Device": auth.sign_device("palmetto", "g")}
    assert c.get("/v1/projects/leads", headers=shop).status_code == 404
    assert c.post("/v1/projects/leads/items", json={"items": [{"title": "x"}]}, headers=env.ipad).status_code == 404

    seen = c.get("/v1/projects/leads", headers=dev).get_json()
    assert (seen["me"], seen["admin"], seen["device"] is not None, "emails" in seen["members"][0]) == ("m2", False, True, False)
    made = c.post("/v1/projects/leads/items", headers=sam, json={"items": [
        {"title": "Order QR codes", "owners": ["m2"], "tags": ["Houston", "Mars"], "due": "2026-11-25"}]})
    item = made.get_json()["items"][0]
    assert (item["tags"], item["due"], item["status"], item["created_by"]) == (["Houston"], "2026-11-25", "open", "m2")
    bad = c.post("/v1/projects/leads/items", json={"items": [{"title": "x", "owners": [env.boss["id"]]}]}, headers=lee)
    assert bad.status_code == 400

    env.clock.at += datetime.timedelta(hours=1)
    done = c.patch(f"/v1/projects/leads/items/{item['id']}", json={"status": "done", "title": "Order 20 QR codes"},
                   headers=lee).get_json()
    assert (done["status"], done["done_by"], done["title"]) == ("done", "cu77", "Order 20 QR codes")
    assert done["log"][-1]["was"] == {"title": "Order QR codes", "status": "open"}
    noted = c.post(f"/v1/projects/leads/items/{item['id']}/notes", json={"text": "Arrived"}, headers=sam).get_json()
    assert [(n["by"], n["text"]) for n in noted["notes"]] == [("m2", "Arrived")]

    # Only its admin changes who is on it; Google sign-in follows, and the admin always stays.
    assert c.put("/v1/projects/leads/members", json={"members": []}, headers=sam).status_code == 403
    env.grants.clear()
    after = c.put("/v1/projects/leads/members", headers=lee,
                  json={"members": [{"name": "Ana Lead", "email": "Ana@Gmail.example"}]}).get_json()
    assert [m["name"] for m in after["members"]] == ["Lee Manager", "Ana Lead"]
    assert env.grants == [("ana@gmail.example", True), (SAM, False)]
    assert c.get("/v1/projects/leads", headers=sam).status_code == 404
    assert len(env.tasks.docs.get(st.TRACKERS, "leads")["history"]) == 2
    assert c.delete(f"/v1/projects/leads/items/{item['id']}", headers=lee).status_code == 200


def test_a_problem_reported_in_a_project_goes_to_the_store_of_whoever_runs_it(env, monkeypatch):
    sent = []
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("STORE_TASKS_FEEDBACK_SLACK_USER", "U1")
    monkeypatch.setattr(notify, "_slack", lambda token, method, payload: sent.append((method, payload)) or {"channel": {"id": "D1"}})
    c = env.client
    lee, sam = _project(env)
    report = {"kind": "problem", "text": "Cannot add an item", "context": {"screen": "Project · All"}}
    # Lee has no store account, so the project has nowhere to send it yet.
    assert c.post("/v1/projects/leads/feedback", json=report, headers=sam).status_code == 400
    tr.Tracker(env.tasks).create("leads", "Leadership", [
        {"id": "o1", "name": "Lee Owner", "emails": [OWNER]},
        {"id": "m2", "name": "Dolce Johnson", "emails": [SAM]},
    ], ["o1"], [], "test")
    assert c.post("/v1/projects/leads/feedback", json=report, headers={"X-Test-Google": "stranger@gmail.example"}).status_code == 404
    assert c.post("/v1/projects/leads/feedback", json=report, headers=sam).status_code == 200
    f = c.get("/v1/feedback", headers=env.boss_h).get_json()["feedback"][0]
    assert (f["staff_name"], f["device"], f["context"]["project"]) == ("Dolce Johnson", "Leadership", "leads")
    assert "Dolce Johnson on Leadership" in [p for m, p in sent if m == "chat.postMessage"][0]["text"]


def test_project_tasks_take_photos_that_only_members_can_see(env):
    lee, sam = _project(env)
    c = env.client
    item = c.post("/v1/projects/leads/items", json={"items": [{"title": "Fix the sign"}]}, headers=lee).get_json()["items"][0]
    url = f"/v1/projects/leads/items/{item['id']}"
    shot = lambda: {"photo": (io.BytesIO(JPEG), "shot.jpg", "image/jpeg")}
    got = c.post(f"{url}/photo", data=shot(), headers=sam, content_type="multipart/form-data").get_json()
    path = got["photos"][0]["path"]
    assert path.startswith("_projects/leads/") and got["photos"][0]["by"] == "m2"
    assert c.get("/v1/projects/leads", headers=lee).get_json()["items"][0]["photos"][0]["path"] == path
    assert c.get(f"/v1/projects/leads/photos/{path}", headers=lee).status_code == 200
    assert c.get(f"/v1/projects/leads/photos/{path}?size=thumb", headers=sam).status_code == 200
    assert c.get(f"/v1/projects/leads/photos/{path}", headers={"X-Test-Google": "stranger@gmail.example"}).status_code == 404
    assert c.get(f"/v1/photos/{path}", headers=env.boss_h).status_code == 404
    assert c.get(f"/v1/projects/leads/photos/palmetto/x", headers=lee).status_code == 404
    # The person who added it or whoever runs the project may remove it.
    mine = c.post(f"{url}/photo", data=shot(), headers=lee, content_type="multipart/form-data").get_json()["photos"][1]["path"]
    assert c.delete(f"{url}/photos/{mine}", headers=sam).status_code == 403
    assert [x["path"] for x in c.delete(f"{url}/photos/{path}", headers=lee).get_json()["photos"]] == [mine]


def _second_store(env) -> dict:
    """Houston, set up as `admin` does: its config, Austin's tasks, and the owner added as a manager there."""
    cfg = json.loads((pathlib.Path(__file__).parent / "stores" / "houston.json").read_text())
    env.tasks.put_store(model.StoreConfig.from_dict(cfg))
    assert admin.copy_templates(env.tasks, "palmetto", "houston") == 5
    boss = env.tasks.save_staff("houston", {"name": "Lee Manager", "role": "manager"}, "test")
    env.tasks.add_store_access(OWNER, "houston", boss["id"], "test")
    return boss


def test_copy_templates_keeps_subtasks_under_their_copied_parent(env):
    _second_store(env)
    rows = {t.title: t for t in env.tasks.templates("houston")}
    assert rows["Thu: blender"].parent_id == rows["Deep Clean"].id != "t2"
    assert rows["Thu: blender"].weekdays == (3,) and rows["Thu: blender"].needs_photo
    assert env.tasks.store("houston").count_items == env.tasks.store("palmetto").count_items
    with pytest.raises(svc.Invalid):
        admin.copy_templates(env.tasks, "palmetto", "houston")


def test_person_in_two_stores_switches_between_them(env):
    hou_boss = _second_store(env)
    c, google = env.client, {"X-Test-Google": OWNER}
    stores = c.get("/v1/projects", headers=google).get_json()["stores"]
    assert [s["id"] for s in stores] == ["palmetto", "houston"]
    boot = c.get("/v1/bootstrap", headers={**google, "X-Store": "houston"}).get_json()
    assert boot["store"]["id"] == "houston" and boot["own"]["staff"]["id"] == hou_boss["id"]
    hou = {**google, "X-Store": "houston", "X-Staff-Session": boot["own"]["session"]}
    made = c.post("/v1/templates", json={"shift": "closing", "title": "Lock back door"}, headers=hou)
    assert made.status_code == 200
    assert "Lock back door" in {t.title for t in env.tasks.templates("houston")}
    assert "Lock back door" not in {t.title for t in env.tasks.templates("palmetto")}
    assert c.get("/v1/bootstrap", headers=google).get_json()["store"]["id"] == "palmetto"
    # A change queued while on Austin still lands in Austin after switching to Houston.
    aus = c.get("/v1/bootstrap", headers=google).get_json()["own"]["session"]
    c.post("/v1/templates", json={"shift": "closing", "title": "Queued on Austin"}, headers={**hou, "X-Staff-Session": aus})
    assert "Queued on Austin" in {t.title for t in env.tasks.templates("palmetto")}


def test_store_not_on_the_account_is_refused(env):
    _second_store(env)
    assert env.client.get("/v1/bootstrap", headers={"X-Test-Google": OWNER, "X-Store": "dallas"}).status_code == 403
    assert env.client.get("/v1/bootstrap", headers={**env.ipad, "X-Store": "houston"}).status_code == 403


def test_store_without_a_clickup_chat_posts_no_summaries(env):
    _second_store(env)
    sent = []
    assert summaries.post_due(env.tasks, "houston", lambda ch, text: sent.append(ch)) == [] and not sent
