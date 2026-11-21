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
    GET|POST /v1/templates, /v1/staff, /v1/accounts, GET /v1/history   (manager)
    POST /webhooks/clickup/<s>/<list> ClickUp change (signed with that webhook's secret)
    POST /internal/tick              scheduled: alerts, shared-shift sync, ClickUp webhooks + mirror, chat summaries
    POST /internal/stores/<s>/check-shifts | mirror | sync-shifts | tasks, GET …/closing-counts
"""

from __future__ import annotations

import datetime
import functools
import hmac
import logging
import os
from urllib.parse import quote

from flask import Flask, Response, g, jsonify, redirect, request

from skills.google_signin import access, identity

from skills.clickup_chat import runner as clickup_chat

from cloud.store_tasks import auth, clickup_import, clickup_webhooks, shared_sync, summaries
from cloud.store_tasks import service as svc
from cloud.store_tasks import store as st

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("store_tasks")

SESSION_HEADER = "X-Staff-Session"  # not Authorization: Google sign-in reads that header itself
DEVICE_HEADER = "X-Shop-Device"
SYNC_EVERY = datetime.timedelta(hours=1)
MIRROR_EVERY = datetime.timedelta(hours=2)
WEBHOOKS_EVERY = datetime.timedelta(hours=6)


def create_app(tasks: svc.Tasks | None = None, signed_in=None, signin_access=None) -> Flask:
    """``signed_in(headers) -> email`` and ``signin_access`` (the Google sign-in list) are injectable for tests."""
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = svc.MAX_PHOTO_BYTES + 64 * 1024
    tasks = tasks or svc.from_env()
    signin_service = os.environ.get("STORE_TASKS_SIGNIN_SERVICE", "store-tasks-app")
    aud = identity.audience(signin_service)
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
            g.account = tasks.device_account(device) if device else tasks.account(signed_in(request.headers))
            g.has_device = bool(device)
            g.store = g.account["store"]
            return fn(*a, **kw)
        return wrapped

    def staff_required(fn):
        @functools.wraps(fn)
        @account_required
        def wrapped(*a, **kw):
            claims = auth.read_session(request.headers.get(SESSION_HEADER, ""), tasks.now().timestamp())
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
        found = tasks.photos.get(path)
        if found is None:
            raise svc.NotFound("photo")
        return Response(found[0], mimetype=found[1], headers={"Cache-Control": "private, max-age=86400"})

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
        tasks.remove_account(g.store, email)
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

    @app.put("/v1/shop-code")
    @manager_required
    def set_shop_code():
        tasks.set_shop_code(g.store, str(body().get("code", "")), g.staff["sid"])
        # Changed from the shop device itself: it keeps working with the new code.
        return jsonify(**tasks.shop_code(g.store), url=os.environ.get("STORE_TASKS_HOME_URL", ""),
                       device=tasks.renew_device(g.account, g.has_device and bool(g.account.get("shop"))))

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
            if chat:
                out[sid]["posted"] = summaries.post_due(tasks, sid, clickup_chat.post_message)
        return jsonify(out)

    return app
