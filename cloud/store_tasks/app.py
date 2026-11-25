"""Store Tasks API — recurring shift checklists for the stores (replaces ClickUp tasks).

Single responsibility: task definitions, daily lists, completions, closing counts, photos.
Every /v1 call carries Google's signed sign-in header (the staff app sits behind Google sign-in
and forwards it), so the API itself knows which Google account is calling.
The staff app (apps/store-tasks) is its only interactive client; Slack and the warehouse
consume its events. See cloud/store_tasks/README.md.

    GET  /v1/bootstrap               store, shifts, staff names (any allowed Google account)
    POST /v1/sessions                staff id + PIN → session (shared store account)
    POST /v1/shop/enroll             shop code → device token (a shop device, no Google account)
    GET  /v1/handoff                 via the Google-gated service → back to the one link with a device token
    GET  /v1/days/<date>             shifts, teams, tasks
    POST /v1/tasks/<id>/complete | reopen | photo
    POST /v1/tasks                   one-off task
    GET|PUT /v1/counts/<date>        closing count
    POST /v1/feedback                a problem or idea, with what was on screen and the last steps
    POST /v1/projects/<id>/feedback  the same from a project; it goes to the store of whoever runs it
    POST /v1/projects/<id>/items/<item>/photo, DELETE …/photos/<path>, GET /v1/projects/<id>/photos/<path>
    GET|POST /v1/templates (+ /order), /v1/staff, /v1/accounts, GET /v1/history, GET /v1/feedback   (manager)
    GET /v1/shop-codes, PUT /v1/shop-code and /v1/shop-codes/<store>  manager shop codes (plaintext; omit code to issue)
    /v1/projects[/<id> (+ /parse, /items, /items/<id>/notes, /members)]   private projects, by Google account (members)
    POST /webhooks/clickup/<s>/<list> ClickUp change (signed with that webhook's secret)
    POST /internal/tick              scheduled: alerts, shared-shift sync, ClickUp webhooks + mirror, chat summaries
    POST /internal/stores/<s>/check-shifts | mirror | sync-shifts | tasks, GET …/closing-counts
"""

from __future__ import annotations

import datetime
import functools
import hmac
import json
import logging
import os
from urllib.parse import quote

from flask import Flask, Response, g, jsonify, redirect, request

from skills.google_signin import access, identity

from skills.clickup_chat import runner as clickup_chat

from cloud.store_tasks import auth, clickup_import, clickup_webhooks, notify, shared_sync, summaries
from cloud.store_tasks import service as svc
from cloud.store_tasks import tracker as tr
from cloud.store_tasks import store as st

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("store_tasks")

SESSION_HEADER = "X-Staff-Session"  # not Authorization: Google sign-in reads that header itself
DEVICE_HEADER = "X-Shop-Device"
STORE_HEADER = "X-Store"  # which of a person's stores the request is for; absent = the account's home store
SYNC_EVERY = datetime.timedelta(hours=1)
MIRROR_EVERY = datetime.timedelta(hours=2)
WEBHOOKS_EVERY = datetime.timedelta(hours=6)


def create_app(tasks: svc.Tasks | None = None, signed_in=None, signin_access=None) -> Flask:
    """``signed_in(headers) -> email`` and ``signin_access`` (the Google sign-in list) are injectable for tests."""
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = svc.MAX_VIDEO_BYTES + 64 * 1024  # each upload checks its own limit
    tasks = tasks or svc.from_env()
    signin_service = os.environ.get("STORE_TASKS_SIGNIN_SERVICE", "store-tasks-app")
    aud = identity.audience(signin_service)
    tracker = tr.Tracker(tasks)
    signed_in = signed_in or (lambda headers: identity.email_from_headers(headers, aud))
    signin_access = signin_access or access.Access(signin_service)

    # ── plumbing ────────────────────────────────────────────────────────────

    @app.errorhandler(identity.NotSignedIn)
    def on_signed_out(e):
        logging.warning("STORE_TASKS_SIGNIN_REJECTED path=%s reason=%s", request.path, e)
        return jsonify(error=str(e)), 401

    @app.errorhandler(auth.AuthError)
    def on_auth(e: auth.AuthError):
        return jsonify(error=str(e)), e.status

    @app.errorhandler(svc.NotFound)
    def on_missing(e):
        return jsonify(error=f"not found: {e}"), 404

    @app.errorhandler(svc.Invalid)
    def on_invalid(e):
        return jsonify(error=str(e)), 400

    @app.errorhandler(KeyError)
    @app.errorhandler(ValueError)
    def on_bad_input(e):
        return jsonify(error=f"bad request: {e}"), 400

    def account_required(fn):
        @functools.wraps(fn)
        def wrapped(*a, **kw):
            device = request.headers.get(DEVICE_HEADER, "")
            g.home = tasks.device_account(device) if device else tasks.account(signed_in(request.headers))
            g.account = tasks.account_in(g.home, request.headers.get(STORE_HEADER))
            g.has_device = bool(device)
            g.store = g.account["store"]
            return fn(*a, **kw)
        return wrapped

    def staff_required(fn):
        @functools.wraps(fn)
        @account_required
        def wrapped(*a, **kw):
            claims = auth.read_session(request.headers.get(SESSION_HEADER, ""), tasks.now().timestamp())
            if claims["store"] != g.store and claims["acct"] == g.account["email"]:
                # A change queued offline in another of the person's stores carries that store's session.
                g.account = tasks.account_in(g.home, claims["store"])
                g.store = g.account["store"]
            if claims["store"] != g.store or claims["acct"] != g.account["email"]:
                raise auth.AuthError("sign in again")
            g.staff = claims
            return fn(*a, **kw)
        return wrapped

    def manager_required(fn):
        @functools.wraps(fn)
        @staff_required
        def wrapped(*a, **kw):
            if g.staff["role"] != auth.MANAGER:
                raise auth.AuthError("managers only", 403)
            return fn(*a, **kw)
        return wrapped

    def internal_required(fn):
        @functools.wraps(fn)
        def wrapped(*a, **kw):
            want = os.environ.get("STORE_TASKS_INTERNAL_TOKEN", "")
            got = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            if len(want) < 24 or not hmac.compare_digest(got, want):
                raise auth.AuthError("unauthorized")
            return fn(*a, **kw)
        return wrapped

    def body() -> dict:
        return request.get_json(silent=True) or {}

    def day_arg(value: str) -> datetime.date:
        return datetime.date.fromisoformat(value)

    # ── device and session ──────────────────────────────────────────────────

    @app.get("/health")
    def health():
        return jsonify(ok=True, service="store-tasks")

    @app.get("/health/ready")
    def ready():
        # Cloud Run's startup probe. A new instance's first Firestore and Storage calls open channels
        # and fetch tokens (seconds); paying that here keeps it out of the first staff request.
        tasks.docs.get(st.STORES, "_warm")
        tasks.photos.get("_warm")
        return jsonify(ok=True)

    @app.get("/v1/bootstrap")
    @account_required
    def bootstrap():
        cfg = tasks.store(g.store)
        staff = tasks.staff(g.store)
        today = cfg.today(tasks.now())
        return jsonify(
            store={"id": cfg.id, "name": cfg.name, "tz": cfg.tz, "cutover_date": cfg.cutover_date,
                   "shifts": [s.to_dict() for s in cfg.shifts], "count_items": list(cfg.count_items)},
            account={"email": g.account["email"], "label": g.account.get("label") or "",
                     "shop": bool(g.account.get("shop"))},
            own=tasks.own_session(g.account),
            device=tasks.renew_device(g.account, g.has_device),
            staff=[svc.public_staff(s) for s in staff],
            on_today=sorted({m["id"] for team in tasks.teams(cfg, today, staff).values() for m in team if m["id"]}),
            today=today.isoformat(),
        )

    @app.post("/v1/sessions")
    @account_required
    def sessions():
        b = body()
        return jsonify(tasks.sign_in(g.account, b.get("staff_id", ""), b.get("pin", "")))

    @app.post("/v1/shop/enroll")
    def enroll_shop():
        return jsonify(tasks.enroll_shop(str(body().get("code", ""))))

    @app.get("/v1/handoff")
    def handoff():
        """Reached through the Google-gated service: back to the one link, carrying a device token.

        The token rides in the URL fragment, which browsers never send to a server."""
        home = os.environ.get("STORE_TASKS_HOME_URL", "").rstrip("/")
        if not home:
            raise auth.AuthError("sign-in is not configured", 500)
        try:
            return redirect(f"{home}/#signin={tasks.google_device(signed_in(request.headers))}")
        except (identity.NotSignedIn, auth.AuthError) as e:
            return redirect(f"{home}/#signin_error={quote(str(e))}")

    # ── days and tasks ──────────────────────────────────────────────────────

    @app.get("/v1/days/<date>")
    @staff_required
    def day(date: str):
        return jsonify(tasks.day(g.store, day_arg(date)))

    @app.post("/v1/tasks/<task_id>/complete")
    @staff_required
    def complete(task_id: str):
        b = body()
        return jsonify(tasks.complete(g.store, task_id, g.staff, b.get("note", ""), b.get("at")))

    @app.post("/v1/tasks/<task_id>/reopen")
    @staff_required
    def reopen(task_id: str):
        return jsonify(tasks.reopen(g.store, task_id, g.staff))

    def upload() -> tuple[bytes, str]:
        f = request.files.get("photo")
        if f is None:
            raise svc.Invalid("no photo")
        return f.read(), f.mimetype or ""

    @app.get("/v1/tasks/<task_id>")
    @staff_required
    def task_detail(task_id: str):
        return jsonify(tasks.task_detail(g.store, task_id))

    @app.delete("/v1/tasks/<task_id>")
    @staff_required
    def remove_task(task_id: str):
        tasks.remove_one_off(g.store, task_id, g.staff)
        return jsonify(ok=True)

    @app.post("/v1/assign")
    @staff_required
    def assign():
        b = body()
        return jsonify(tasks=tasks.assign(g.store, b.get("task_ids") or [], b.get("assignee_id"), g.staff))

    @app.post("/v1/tasks/<task_id>/comments")
    @staff_required
    def comment(task_id: str):
        return jsonify(tasks.comment(g.store, task_id, g.staff, body().get("text", "")))

    @app.post("/v1/tasks/<task_id>/photo")
    @staff_required
    def photo(task_id: str):
        return jsonify(tasks.add_photo(g.store, task_id, g.staff, *upload()))

    @app.delete("/v1/tasks/<task_id>/photos/<path:path>")
    @staff_required
    def remove_photo(task_id: str, path: str):
        return jsonify(tasks.remove_photo(g.store, task_id, path, g.staff))

    @app.post("/v1/tasks/<task_id>/instructions")
    @manager_required
    def set_instructions(task_id: str):
        return jsonify(tasks.set_instructions(g.store, task_id, body().get("instructions", ""), g.staff))

    @app.post("/v1/tasks/<task_id>/reference-photo")
    @manager_required
    def add_reference_photo(task_id: str):
        return jsonify(tasks.add_reference_photo(g.store, task_id, g.staff, *upload()))

    @app.delete("/v1/tasks/<task_id>/reference-photos/<path:path>")
    @manager_required
    def remove_reference_photo(task_id: str, path: str):
        return jsonify(tasks.remove_reference_photo(g.store, task_id, path))

    @app.get("/v1/photos/<path:path>")
    @staff_required
    def get_photo(path: str):
        if not path.startswith(f"{g.store}/"):
            raise svc.NotFound("photo")
        found = tasks.photo(path, request.args.get("size"))
        if found is None:
            raise svc.NotFound("photo")
        # A path never changes what it holds, so the device may keep it.
        return Response(found[0], mimetype=found[1], headers={"Cache-Control": "private, max-age=604800, immutable"})

    @app.post("/v1/tasks")
    @staff_required
    def one_off():
        return jsonify(tasks.add_one_off(g.store, body(), g.staff["sid"]))

    # ── closing count ───────────────────────────────────────────────────────

    @app.get("/v1/counts/<date>")
    @staff_required
    def get_count(date: str):
        return jsonify(count=tasks.count(g.store, day_arg(date)))

    @app.put("/v1/counts/<date>")
    @staff_required
    def put_count(date: str):
        return jsonify(count=tasks.submit_count(g.store, day_arg(date), body(), g.staff))

    @app.post("/v1/counts/<date>/photo")
    @staff_required
    def count_photo(date: str):
        return jsonify(count=tasks.add_count_photo(g.store, day_arg(date), g.staff, *upload()))

    # ── manager ─────────────────────────────────────────────────────────────

    @app.get("/v1/templates")
    @manager_required
    def templates():
        return jsonify(templates=[t.to_dict() for t in tasks.templates(g.store)])

    @app.post("/v1/templates")
    @manager_required
    def save_template():
        return jsonify(template=tasks.save_template(g.store, body(), g.staff["sid"]).to_dict())

    def feedback_body() -> tuple[dict, tuple[bytes, str] | None]:
        if request.files.get("photo") is not None:
            return json.loads(request.form.get("data") or "{}"), upload()
        return body(), None

    @app.post("/v1/feedback")
    @staff_required
    def add_feedback():
        data, photo = feedback_body()
        f = tasks.add_feedback(g.store, g.staff, g.account, data, photo)
        ctx = f["context"]
        log.info("store-tasks feedback id=%s store=%s staff=%s kind=%s screen=%s date=%s shift=%s task_id=%s",
                 f["id"], g.store, f["staff_id"], f["kind"], ctx.get("screen"), ctx.get("date"),
                 ctx.get("shift"), ctx.get("task_id"))
        notify.feedback(tasks.store(g.store), f)
        return jsonify(id=f["id"])

    @app.get("/v1/feedback")
    @manager_required
    def feedback():
        return jsonify(feedback=tasks.feedback(g.store))

    @app.post("/v1/feedback/<fid>/done")
    @manager_required
    def close_feedback(fid: str):
        return jsonify(tasks.close_feedback(g.store, fid, g.staff["sid"], bool(body().get("done", True))))

    @app.post("/v1/templates/order")
    @manager_required
    def reorder_templates():
        b = body()
        return jsonify(changed=tasks.reorder_templates(g.store, str(b.get("shift", "")), list(b.get("ids") or []),
                                                       g.staff["sid"]))

    # ── projects: apart from the stores; a member is known by their own Google account ──

    def person_required(fn):
        @functools.wraps(fn)
        def wrapped(*a, **kw):
            device = request.headers.get(DEVICE_HEADER, "")
            if device:
                claims = auth.read_device(device, tasks.now().timestamp())
                if claims["kind"] != auth.GOOGLE:
                    raise svc.NotFound("project")  # a shop device never sees a project
                g.email, g.has_device = claims["email"], True
            else:
                g.email, g.has_device = signed_in(request.headers).strip().lower(), False
            return fn(*a, **kw)
        return wrapped

    def grant(emails: list[str], allowed: bool) -> None:
        for e in emails:
            if not allowed and (tasks.docs.get(st.ACCOUNTS, e) or tracker.projects(e)):
                continue  # still signs in for a store or another project
            signin_access.set(e, allowed)

    @app.get("/v1/projects")
    @person_required
    def projects():
        """Everywhere this Google account can go: its store's tasks (if any) and its projects, for the switcher."""
        acct = tasks.docs.get(st.ACCOUNTS, g.email)
        stores = []
        if acct and acct.get("active", True):
            stores = [{"id": s, "name": tasks.store(s).name} for s in [acct["store"], *sorted(acct.get("also") or {})]]
        return jsonify(store=stores[0] if stores else None, stores=stores, projects=tracker.projects(g.email))

    @app.get("/v1/projects/<pid>")
    @person_required
    def project(pid: str):
        device = auth.sign_google(g.email, tasks.now().timestamp()) if g.has_device else None
        return jsonify({**tracker.view(pid, g.email), "device": device})

    @app.post("/v1/projects/<pid>/parse")
    @person_required
    def project_parse(pid: str):
        return jsonify(items=tracker.parse(pid, g.email, body().get("text", "")))

    @app.post("/v1/projects/<pid>/items")
    @person_required
    def project_add(pid: str):
        return jsonify(items=tracker.add(pid, g.email, body().get("items") or []))

    @app.patch("/v1/projects/<pid>/items/<item_id>")
    @person_required
    def project_update(pid: str, item_id: str):
        return jsonify(tracker.update(pid, g.email, item_id, body()))

    @app.post("/v1/projects/<pid>/items/<item_id>/notes")
    @person_required
    def project_note(pid: str, item_id: str):
        return jsonify(tracker.note(pid, g.email, item_id, body().get("text", "")))

    @app.post("/v1/projects/<pid>/items/<item_id>/photo")
    @person_required
    def project_photo(pid: str, item_id: str):
        return jsonify(tracker.add_photo(pid, g.email, item_id, *upload()))

    @app.delete("/v1/projects/<pid>/items/<item_id>/photos/<path:path>")
    @person_required
    def project_remove_photo(pid: str, item_id: str, path: str):
        return jsonify(tracker.remove_photo(pid, g.email, item_id, path))

    @app.get("/v1/projects/<pid>/photos/<path:path>")
    @person_required
    def project_get_photo(pid: str, path: str):
        data, mimetype = tracker.photo(pid, g.email, path, request.args.get("size"))
        return Response(data, mimetype=mimetype, headers={"Cache-Control": "private, max-age=604800, immutable"})

    @app.delete("/v1/projects/<pid>/items/<item_id>")
    @person_required
    def project_remove(pid: str, item_id: str):
        tracker.remove(pid, g.email, item_id)
        return jsonify(ok=True)

    @app.post("/v1/projects/<pid>/feedback")
    @person_required
    def project_feedback(pid: str):
        """Report a problem from a project: it lands with the problems of the store whose manager runs it."""
        p, mid = tracker.member(pid, g.email)
        store_id = tracker.home(p)
        if store_id is None:
            log.error("store-tasks fail reason=project_feedback_no_store project=%s", pid)
            raise svc.Invalid("this project has nowhere to send problems yet")
        data, photo = feedback_body()
        context = data.get("context") if isinstance(data.get("context"), dict) else {}
        name = next(m["name"] for m in p["members"] if m["id"] == mid)
        f = tasks.add_feedback(store_id, {"sid": mid}, {"email": g.email, "label": p["name"]},
                               {**data, "context": {**context, "project": pid}}, photo, name=name)
        log.info("store-tasks feedback id=%s store=%s project=%s member=%s kind=%s screen=%s",
                 f["id"], store_id, pid, mid, f["kind"], f["context"].get("screen"))
        notify.feedback(tasks.store(store_id), f)
        return jsonify(id=f["id"])

    @app.put("/v1/projects/<pid>/members")
    @person_required
    def project_members(pid: str):
        _, added, removed = tracker.set_members(pid, g.email, body().get("members") or [])
        try:
            grant(added, True)
        except Exception as e:
            log.error("store-tasks fail reason=project_signin_grant project=%s err=%r", pid, e)
            raise svc.Invalid("Google would not add one of those accounts — check each is a real Google account")
        grant(removed, False)
        return jsonify(tracker.view(pid, g.email))

    @app.get("/v1/staff")
    @manager_required
    def staff():
        return jsonify(staff=[svc.public_staff(s) for s in tasks.staff(g.store, include_inactive=True)])

    @app.post("/v1/staff")
    @manager_required
    def save_staff():
        return jsonify(staff=svc.public_staff(tasks.save_staff(g.store, body(), g.staff["sid"])))

    @app.post("/v1/staff/<staff_id>/pin")
    @manager_required
    def set_pin(staff_id: str):
        tasks.set_pin(g.store, staff_id, body().get("pin", ""))
        return jsonify(ok=True)

    @app.get("/v1/accounts")
    @manager_required
    def accounts():
        return jsonify(accounts=tasks.accounts(g.store))

    @app.post("/v1/accounts")
    @manager_required
    def save_account():
        acct = tasks.save_account(g.store, body(), g.staff["sid"])
        try:
            signin_access.set(acct["email"], True)
        except Exception as e:
            tasks.remove_account(g.store, acct["email"])
            log.error("store-tasks fail reason=signin_grant email_domain=%s err=%r", acct["email"].split("@")[-1], e)
            raise svc.Invalid("Google would not add that account — check the address is a real Google account")
        return jsonify(account=acct)

    @app.delete("/v1/accounts/<email>")
    @manager_required
    def remove_account(email: str):
        email = email.strip().lower()
        if email == g.account["email"]:
            raise svc.Invalid("you are signed in with this account — remove it from another one")
        if not tasks.remove_account(g.store, email):
            return jsonify(ok=True)  # still opens another store, so Google sign-in stays
        try:
            signin_access.set(email, False)
        except Exception as e:
            # The account is already refused by this API; only Google's login page still lets it through.
            log.error("store-tasks fail reason=signin_revoke email_domain=%s err=%r", email.split("@")[-1], e)
        return jsonify(ok=True)

    @app.get("/v1/shop-code")
    @manager_required
    def shop_code():
        return jsonify(**tasks.shop_code(g.store), url=os.environ.get("STORE_TASKS_HOME_URL", ""))

    @app.get("/v1/shop-codes")
    @manager_required
    def shop_codes():
        """Every store this manager can open, with plaintext shop codes (legacy rows need rotate)."""
        return jsonify(
            stores=tasks.shop_codes_for(g.home, session_store=g.store, session_role=g.staff["role"]),
            url=os.environ.get("STORE_TASKS_HOME_URL", ""),
        )

    @app.put("/v1/shop-code")
    @manager_required
    def set_shop_code():
        """Set digits, or omit ``code`` to issue a random one. Optional ``store`` for another managed store."""
        b = body()
        store_id = str(b.get("store") or g.store)
        tasks.require_manager_on(
            g.home, store_id, session_store=g.store, session_role=g.staff["role"])
        code = b.get("code")
        tasks.set_shop_code(store_id, None if code in (None, "") else str(code), g.staff["sid"])
        # Changed from the shop device itself: it keeps working with the new code.
        on_shop = g.has_device and bool(g.account.get("shop")) and g.account["store"] == store_id
        return jsonify(**tasks.shop_code(store_id), url=os.environ.get("STORE_TASKS_HOME_URL", ""),
                       device=tasks.renew_device(g.account, on_shop))

    @app.put("/v1/shop-codes/<store_id>")
    @manager_required
    def set_shop_code_for(store_id: str):
        tasks.require_manager_on(
            g.home, store_id, session_store=g.store, session_role=g.staff["role"])
        code = body().get("code")
        tasks.set_shop_code(store_id, None if code in (None, "") else str(code), g.staff["sid"])
        on_shop = g.has_device and bool(g.account.get("shop")) and g.account["store"] == store_id
        return jsonify(**tasks.shop_code(store_id), url=os.environ.get("STORE_TASKS_HOME_URL", ""),
                       device=tasks.renew_device(g.account, on_shop))

    @app.get("/v1/history")
    @manager_required
    def history():
        return jsonify(days=tasks.history(g.store, day_arg(request.args["from"]), day_arg(request.args["to"])))

    # ── ClickUp webhooks ────────────────────────────────────────────────────

    @app.post("/webhooks/clickup/<store_id>/<list_id>")
    def clickup_webhook(store_id: str, list_id: str):
        h = clickup_webhooks.hook(tasks, store_id, list_id)
        if h is None or not clickup_webhooks.signed(h["secret"], request.get_data(), request.headers.get("X-Signature", "")):
            raise auth.AuthError("unauthorized")
        return jsonify(clickup_webhooks.receive(tasks, store_id, list_id, request.get_json(silent=True) or {}))

    # ── internal (Cloud Scheduler, nightly pipeline) ────────────────────────

    @app.post("/internal/stores/<store_id>/check-shifts")
    @internal_required
    def check_shifts(store_id: str):
        return jsonify(alerts=tasks.check_shifts(store_id))

    @app.post("/internal/stores/<store_id>/tasks")
    @internal_required
    def internal_task(store_id: str):
        b = body()
        cfg = tasks.store(store_id)
        day = cfg.today(tasks.now()).isoformat()
        data = {"title": b.get("title"), "shift": b.get("shift"), "source": b.get("source") or "api", "date": day}
        if b.get("key"):
            # The caller's key names the task, so a retried request cannot add it twice.
            data["id"] = f"{store_id}_{day}_o{auth.sha256(str(b['key']))[:12]}"
        return jsonify(tasks.add_one_off(store_id, data, data["source"]))

    @app.get("/internal/stores/<store_id>/closing-counts")
    @internal_required
    def closing_counts(store_id: str):
        return jsonify(tasks=tasks.counts_as_clickup(store_id, request.args.get("since")))

    @app.post("/internal/stores/<store_id>/mirror")
    @internal_required
    def mirror(store_id: str):
        return jsonify(clickup_import.mirror(tasks, store_id))

    @app.post("/internal/stores/<store_id>/sync-shifts")
    @internal_required
    def sync_shifts(store_id: str):
        return jsonify(shared_sync.sync(tasks, store_id))

    @app.post("/internal/tick")
    @internal_required
    def tick():
        """The one scheduled entry point: alerts every call, shared data hourly, ClickUp mirror until
        cutover (sooner when a shift summary waits for fresh ClickUp data), then due chat summaries."""
        out = {}
        chat = bool(os.environ.get("CLICKUP_PAT"))
        root = os.environ.get("STORE_TASKS_PUBLIC_URL") or request.url_root.replace("http://", "https://", 1)
        for store_doc in tasks.docs.query(st.STORES):
            sid = store_doc["id"]
            out[sid] = {"alerts": len(tasks.check_shifts(sid))}
            for job, every, run in (("sync_shifts", SYNC_EVERY, shared_sync.sync),
                                    ("webhooks", WEBHOOKS_EVERY, lambda t, s: clickup_webhooks.ensure(t, s, root)),
                                    ("mirror", MIRROR_EVERY, clickup_import.mirror)):
                if job != "sync_shifts" and not (store_doc.get("clickup") and chat):
                    continue
                if not (tasks.job_due(sid, job, every) or (job == "mirror" and summaries.wants_mirror(tasks, sid))):
                    continue
                started = tasks.now()
                try:
                    out[sid][job] = run(tasks, sid)
                    tasks.job_done(sid, job, started)
                except Exception as e:
                    log.error("store-tasks fail reason=%s store=%s err=%r", job, sid, e)
                    out[sid][job] = "failed"
            if chat and store_doc.get("clickup"):
                try:
                    out[sid]["inbox"] = clickup_webhooks.drain(tasks, sid)
                except Exception as e:
                    log.error("store-tasks fail reason=inbox store=%s err=%r", sid, e)
            try:
                out[sid]["reset_trials"] = tasks.reset_trials(sid)
            except Exception as e:
                log.error("store-tasks fail reason=reset_trials store=%s err=%r", sid, e)
            if chat:
                out[sid]["posted"] = summaries.post_due(tasks, sid, clickup_chat.post_message)
        return jsonify(out)

    return app
