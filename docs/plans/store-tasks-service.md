# Store Tasks — own task service + staff app (replaces ClickUp tasks)

Branch `fix/as-i-am-opening-houston-store` (operator asked to continue here) · Austin first, Houston after.

Consulted: `CONTRIBUTING.md` (dev loop, evidence tiers), `docs/WORKFLOW.md`, `AGENTS.md` (cloud
workers table), `docs/operator-console/ARCHITECTURE.md` §1/§15, `docs/contributing/ui-polish.md`
(D1–D10), `cloud/jarvis_dev_gateway/app.py:1` + `.github/workflows/jarvis-dev-gateway-deploy.yml:1`
(Cloud Run service pattern), `cloud/pup_watch/persist.py:26` (named Firestore database pattern),
`agents/bhaga/scripts/ingest_inventory.py:195` (closing-count ingest).

## Agreed scope (jam, 2026-11-20)

- Own, hosted replacement for ClickUp tasks. 24/7 availability, under $10 a month.
- Single-responsibility **Tasks API** with its own data; a **separate staff front end**; Slack for
  messaging with task ↔ Slack integrations; shared read-only schedule data.
- Full backfill of ClickUp so the move is seamless. Austin first.
- Storage hidden behind the API so a faster store for the Operator Console can follow later.

## Facts from the ClickUp audit (29,150 tasks, 2026-03-06 → 2026-11-20)

| Fact | Value |
|---|---|
| Lists | Opening `901712091488`, Shift Change `901712092659`, Closing `901711373136` |
| Daily task set | 27 Opening, 20 Shift Change, 44 Closing; ordered by custom field `Order #` |
| Recurrence | Daily; weekday deep-clean rotation as subtasks named `Monday: …`; 6 daily bathroom subtasks |
| Due | Opening 07:00, Shift Change 14:00, Closing 18:00–20:00 (America/Chicago) |
| Assignee | One per task, = whoever works that shift that day |
| Closing form | 232 submissions (`closing submission` tag), ~40 free-text counts, tips, notes |
| Photos | 109 on 51 tasks (attachment custom field), none since 2026-05-17 |

## Design

```
apps/store-tasks (static PWA, Cloud Run nginx)  ──HTTPS──▶  cloud/store_tasks (Flask, Cloud Run)
                                                              │  Firestore database `storetasks`
Slack bridge (cloud/store_tasks/slack_bridge.py) ◀─ Pub/Sub ──┤  GCS bucket `jarvis-store-tasks-photos`
BigQuery `bhaga.adp_scheduled_shifts` ──sync──▶ shared_shifts ┘  (read-only shared data)
```

- The API is the only writer of its collections. Storage sits behind `cloud/store_tasks/store.py`
  (`MemoryStore` for tests/local, `FirestoreStore` in prod).
- Auth: Google sign-in in front of the app (Cloud Run direct IAP via `skills/google_signin/`, as the
  Operator Console); an allowed account is a shared store account or one person's own. On a shared
  account staff sign in with name + PIN (scrypt hash, lockout after 5 misses) and get a 12 h HMAC-signed session; `manager` role
  for edits; internal routes use a bearer secret from Secret Manager.
- Daily lists are generated idempotently from templates (doc id `{store}_{date}_{template_id}`).
- Assignment: the shift team = people whose ADP scheduled range overlaps the shift window; the person
  who ticks is recorded from their session.
- Until `cutover_date`, days are mirrored from ClickUp (no double ticking); from that date they are
  generated.
- Events (`task.completed`, `shift.incomplete`, `count.submitted`) are published to Pub/Sub topic
  `store-tasks-events`; the API has no Slack code.

Stubs:

```python
# cloud/store_tasks/model.py
@dataclass(frozen=True)
class Template:
    id: str; store: str; shift: str; title: str; instructions: str; order: float
    weekdays: tuple[int, ...]            # () = every day; 0 = Monday
    parent_id: str | None; needs_photo: bool; active: bool

def due_on(t: Template, day: date) -> bool: ...
def build_day(store: StoreConfig, templates: list[Template], day: date) -> list[dict]: ...
def shift_team(shift: ShiftConfig, scheduled: list[dict]) -> list[str]: ...
```

## Milestone 1 — Tasks API, backfill, daily lists, shared shifts

Model: Sonnet for the service; Opus review for auth.

- New `cloud/store_tasks/{__init__,model,store,auth,events,app,clickup_import,shared_sync}.py`,
  `Dockerfile`, `requirements.txt`, `README.md`, tests beside each module.
- Routes: `GET|POST /v1/accounts`, `POST /v1/sessions`, `GET /v1/stores/<s>/bootstrap`,
  `GET /v1/stores/<s>/days/<date>`, `POST /v1/tasks/<id>/complete|reopen|photo`,
  `GET|PUT /v1/stores/<s>/templates`, `GET /v1/stores/<s>/history`, staff + sign-in account admin,
  `POST /internal/{generate,mirror,sync-shifts}`, `GET /health`.
- `clickup_import.py` — idempotent import keyed by ClickUp task id: templates (from the last 21 days),
  all history, closing submissions, photos to GCS; `--mirror` re-syncs the open window.

**Verify (M1):**

```bash
python3 -m pytest cloud/store_tasks -q
python3 -m cloud.store_tasks.clickup_import --store palmetto --dry-run
python3 -m cloud.store_tasks.verify_backfill --store palmetto
```

Pass criterion: counts per list and status equal ClickUp; 50 sampled tasks match field by field;
generated lists for three weekdays equal ClickUp's (titles, order, weekday item).

## Milestone 2 — Staff app (separate front end)

Model: Sonnet.

- New `apps/store-tasks/` (Vite + React + TypeScript + Tailwind v4, PWA). Tokens and components mirror
  the Operator Console look (`apps/operator-console/components/ui/`): neutral surfaces, 44 px+ tap
  targets, focus rings, pending states. D1–D10 checked: one way to tick, each fact once, plain wording.
- Screens: who-are-you + PIN pad, shift list (progress, pinned deep-clean item),
  task sheet (instructions, photo, note), closing count, manager (tasks, people, sign-in, history).
- Offline: app shell cached by a service worker; ticks queue in IndexedDB and replay with an
  idempotency key, so a retry never duplicates.

**Verify (M2):**

```bash
cd apps/store-tasks && npm run lint && npm test && npm run build
python3 -m pytest cloud/store_tasks -q
```

Pass criterion: happy path on an iPad viewport; failure path with the network off mid-shift syncs with
no loss or duplicate; bad PIN and a Google account not on the list are refused.

## Milestone 3 — Closing count → inventory, Slack bridge

Model: Sonnet.

- `GET /v1/stores/<s>/closing-counts` returns submissions in the ClickUp task shape;
  `agents/bhaga/scripts/ingest_inventory.py:246` picks the source from profile key
  `closing_source` (`clickup` default, `store_tasks`).
- `cloud/store_tasks/slack_bridge.py` — Pub/Sub push → Slack: unfinished-shift alert, closing post;
  Slack message action / slash command → one-off task.

**Verify (M3):**

```bash
python3 -m pytest cloud/store_tasks agents/bhaga/scripts/test_ingest_inventory.py -q
python3 -m agents.bhaga.scripts.ingest_inventory --store palmetto --dry-run
```

Pass criterion: a count submitted in the app produces the same `inventory_closing_daily` rows the
ClickUp form would (dry run), and a Slack alert fires once for an unfinished shift.

## Milestone 4 — Deploy, cutover, docs

Model: Sonnet.

- `.github/workflows/store-tasks-deploy.yml` (API + app, scale to zero, smoke), Cloud Scheduler jobs
  for generate / mirror / sync-shifts / unfinished check.
- Austin cutover date set in store config; ClickUp left read-only until 2027-03-04.

**Verify (M4):**

```bash
python3 scripts/verify.py --full
python3 scripts/check_doc_freshness.py
```

## Per-scenario evidence (PR §4)

Evidence tier: sandbox-e2e

| Scenario | Evidence |
|---|---|
| Happy path: backfill | counts per list/status equal ClickUp; 50-task field match |
| Happy path: daily list | three weekdays match ClickUp incl. weekday item and order |
| Assignment | shift team equals ADP schedule for those days |
| Staff flow | iPad: PIN sign-in, tick, photo, complete shift |
| Failure / recovery | network off mid-shift → queued ticks sync once; replay is a no-op |
| Failure: access | no Google sign-in, account not on the list, bad PIN, crew calling manager route → refused |
| Speed | tick paints immediately; shift screen < 1 s after first load |
| Cost | first-week bill projects under $10 a month |
| Demo | operator tries it on real Austin data before `gh pr create` |

## Invariants preserved

- Idempotent upserts everywhere (doc ids are natural keys; completion carries an idempotency key).
- America/Chicago for every day boundary and due time; no hardcoded store values — shift windows,
  cutover date and item lists are store config.
- ClickUp and ADP are read-only from this service. No PII or secrets in git; PINs and tokens hashed.
- Template edits keep an append-only history (who/when).
- Existing behavior must not break: `ingest_inventory` defaults to ClickUp until the profile says otherwise.

## Feature flag

No new flag. The service is additive and out of the nightly path; the only switch that can change
numbers is profile key `closing_source`, which stays `clickup` until cutover.

## Docs lock-step

`AGENTS.md` (cloud workers table), `.cursor/rules/jarvis.mdc`, `RUNBOOK.md` (new § Store Tasks),
`cloud/store_tasks/README.md`, `apps/store-tasks/README.md`, `agents/bhaga/scripts/README.md`,
`PROGRESS.md`; run `python3 scripts/check_doc_freshness.py`.

## Branch / PR mechanics

Single branch; push as the bot account `jarvis-agent-bot328`; `gh pr create --base main`; babysit with
`scripts/pr_triage.py`; never self-merge — operator merges.
