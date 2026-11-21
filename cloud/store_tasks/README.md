# Store Tasks

Shift checklists for the stores (opening, shift change, closing), the closing count, and
deep-clean photos. Replaces ClickUp tasks. Three deployables, one job each:

| Piece | Code | Runs as | Job |
|---|---|---|---|
| Tasks API | `cloud/store_tasks/` | Cloud Run `store-tasks-api` | The only reader and writer of task data. All rules live here. |
| Staff app | `apps/store-tasks/` | Cloud Run `store-tasks-app` (static files, Google sign-in) | What managers and Google accounts use. Talks only to the Tasks API. |
| Shop app | the same image | Cloud Run `store-tasks-shop` (static files, no Google sign-in) | What the shop iPad uses, signed in with the store's shop code. |
| Slack bridge | `cloud/store_tasks_slack/` | Cloud Run `store-tasks-slack` | Turns task events into Slack posts and Slack messages into tasks. |

Nothing here imports from, or is imported by, BHAGA or the Operator Console. Other systems
reach it through its HTTP API or its events.

```
iPad (staff app) ──HTTPS──▶ Tasks API ──▶ Firestore database "storetasks"   (tasks, templates, staff, counts)
                               │      ──▶ GCS jarvis-store-tasks-photos     (photos)
                               │      ──▶ Pub/Sub store-tasks-events ──▶ Slack bridge ──▶ Slack
                               ◀── BigQuery adp_scheduled_shifts (read-only copy in shared_shifts)
nightly inventory (BHAGA) ──GET /internal/stores/<s>/closing-counts──▶ Tasks API
```

## How it works

- **Templates → daily lists.** A manager edits task templates (title, how-to, shift, order,
  weekdays, photo needed). Each day's list is built from them the first time anyone opens
  the day. Document ids are `{store}_{date}_{template}`, so building twice changes nothing.
  A task that needs a photo cannot be marked done without one, by anyone — a manager who
  wants an exception turns the requirement off for that task. `admin photo-rules` lists the
  tasks ClickUp history says need one (the how-to asks for a photo, or photos on 3+ days).
- **Who ticked.** The app sits behind Google sign-in (Cloud Run direct IAP, the same building
  blocks as the Operator Console: [`skills/google_signin/`](../../skills/google_signin/README.md)).
  A shared account (the store iPad) then asks for a name and PIN; a person's own Google
  account goes straight in as them, no PIN. The tick records that person and the time.
  Sessions last 12 hours and a shared account locks after 3 idle minutes.
- **The shop device.** The shop iPad has no Google account. It opens the shop app and enters
  the store's 6-digit shop code once (a manager sets it in Manage → Sign-in); it then keeps a
  device token, renewed whenever the app loads, and works as "Shop". Crew never sign in there:
  they pick who is doing each task, and a tick from the shop counts for the task's assignee.
  A manager taps Manager and enters their PIN to manage; it falls back to the shop after 3 idle
  minutes. Changing the code signs every shop device out. Ten wrong codes within 15 minutes,
  from anywhere, pause shop sign-in for 15 minutes; devices already signed in keep working.
- **One origin.** The app's nginx passes `/v1/` on to the API, re-sending Google's signed
  assertion as `X-Signin-Assertion` (Google's front end drops `x-goog-*` headers between
  services). The API verifies it against the app's audience, so calling the API directly
  without a Google sign-in is refused.
- **Who is doing it.** Anyone signed in picks a person for a task, or for every unassigned
  task on a list at once, as in ClickUp. Only people Active in the latest ADP directory
  snapshot (`adp_directory_status`) can be picked; the hourly sync sets `adp_active` on each
  person, adds anyone employed who is missing, and changes nothing if the snapshot is empty.
  A ClickUp nickname is linked to its ADP name through the person's `adp_name`. Nothing is assigned
  automatically. The ADP schedule (copied hourly from BigQuery into `shared_shifts`, so the
  tablet never waits on the warehouse; BigQuery stays the source) only puts the people
  scheduled today at the top of the picker.
- **One task.** Opening a task shows how to do it, example photos of how it should look,
  photos from earlier days, today's photos (any task takes photos, from a phone camera or a
  computer's files), its steps and the team's notes. A manager edits the how-to and example
  photos there; on a recurring task the edit applies every day from today. A task added for
  one day can be deleted by whoever added it or a manager.
- **Adding, repeating, copying.** One form creates, copies and changes tasks. Anyone adds a
  task for the day. A manager can make it repeat: every day, chosen weekdays every 1–12
  weeks, or a day of the month, with optional first and last dates. A day's list holds only
  what is due that day. Changing a repeating task's schedule or shift updates lists already
  built from today on (open, untouched tasks only). Duplicate on any task opens the form
  filled in from it, and a copied repeating task keeps its example photos.
- **Any screen.** Phone: one column, a task opens as a sheet. iPad landscape and desktop:
  the list and the open task side by side.
- **Offline.** The app shell and the day are kept on the device. Ticks and assignments made
  offline queue on the device and send when the connection returns; a repeated tick is a
  no-op. Photos and notes need a connection.
- **Closing count.** Same fields as the ClickUp form, including its photos. The nightly inventory job reads it
  through `closing-counts`, which returns the ClickUp form-task shape, so
  `agents/bhaga/scripts/ingest_inventory.py` parses it unchanged. Switch a store with the
  profile key `"closing_source": "store_tasks"` (default `clickup`).
- **Events.** `task.completed`, `task.reopened`, `task.created`, `shift.incomplete`,
  `count.submitted` on Pub/Sub. A failed publish is logged and never fails the tick.

## Access

| Caller | Proof | Can do |
|---|---|---|
| Anyone | none | `GET /health` |
| Google account not on the list | Google sign-in | Nothing (Google refuses; the API also answers 403) |
| Shared account (store iPad) | Google sign-in | See the store's shifts and staff names |
| Shop device | shop code → device token (`X-Shop-Device`) | Tick, assign, add photos and notes as "Shop" (no PIN); cannot add or change tasks; managers sign in on it with their PIN |
| Crew | Google sign-in + PIN session (`X-Staff-Session`) | Read days, tick, undo own ticks, assign anyone, add photos and notes, remove own photos, send the count, add a one-off task and delete their own |
| Manager | Google sign-in + 6-digit PIN session, or their own Google account | Also: how-to text and example photos, templates, people and PINs, sign-in accounts, history, undo anyone's tick, remove any photo or one-off task |
| Scheduler, nightly job, Slack bridge | `Authorization: Bearer` internal token | `/internal/*` |

PINs and shop codes are stored as scrypt hashes; five wrong PINs lock that person for five minutes. An
account can only see its own store. Adding or removing an account in Manage → Sign-in also
adds or removes it on the app's Google sign-in list, so the two never drift.

## Moving off ClickUp

`cutover_date` on the store decides who owns a day: before it, days are mirrored from
ClickUp (read-only here); from it on, days are built here and ClickUp is ignored.

```bash
python3 -m cloud.store_tasks.admin seed --store palmetto            # store config from stores/<id>.json
python3 -m cloud.store_tasks.clickup_import --store palmetto --all  # staff, templates, all history, counts, photos
python3 -m cloud.store_tasks.verify_backfill --store palmetto       # counts and fields vs ClickUp
python3 -m cloud.store_tasks.admin manager --store palmetto --name "First Last" --pin 123456
python3 -m cloud.store_tasks.admin account --store palmetto --email ipad@example.com --label "Front iPad"
python3 -m cloud.store_tasks.admin account --store palmetto --email me@example.com --staff "First Last"   # own account, no PIN
python3 -m cloud.store_tasks.admin cutover --store palmetto --date 2026-12-01
python3 -m cloud.store_tasks.admin photo-rules --store palmetto [--apply]   # tasks that need a photo, from ClickUp history
python3 -m cloud.store_tasks.admin count-sections --store palmetto --channel 8cr6661-737   # closing-count sections, from ClickUp's latest closing update
```

Every import write is keyed by the ClickUp task id, so re-running is an upsert. Until
cutover the scheduled tick re-mirrors the last three days every two hours: fields and status,
then, for tasks ClickUp changed since they were last copied, comments with replies, photos,
videos, documents and checklists (90 seconds of that per run; the rest on the next run; files
over 100 MB are logged and left to `--files`).

```bash
python3 -m cloud.store_tasks.clickup_import --store palmetto --files   # everything on every task, once (hours; resumable)
```

`--files` also keeps each task's raw ClickUp JSON under `archive/<store>/clickup/` in the
photo bucket (never served by the app). Templates are
inferred from what ClickUp scheduled in the last 21 days; ClickUp lists every weekday's
deep-clean item every day, here a "Tues: …" item shows on Tuesdays only.

Moving `cutover_date` later (for example after a trial) removes the lists built here for the
days handed back to ClickUp; run `clickup_import --all` again afterwards to mirror those days.

A new store: add `stores/<id>.json`, run `seed`, add people, add its iPad's Google account.

## Operate

- **Deploy:** `.github/workflows/store-tasks-deploy.yml` on merge to `main`.
- **One-time setup:** `bash cloud/store_tasks/provision.sh` (service account, database,
  bucket, topic, secrets). Run `python3 scripts/gcp_access_probe.py` first.
- **Schedule:** one Cloud Scheduler job, `store-tasks-tick`, every 10 minutes →
  `POST /internal/tick` (late-shift alerts every call, schedule copy hourly, ClickUp mirror
  two-hourly until cutover).
- **Failures** log one line starting `store-tasks fail reason=…` with the store and cause.
- **Local run:** `STORE_TASKS_BACKEND=memory` uses in-memory storage; tests use the same.

```bash
python3 -m pytest cloud/store_tasks cloud/store_tasks_slack -q
cd apps/store-tasks && npm test && npm run build
```

## ClickUp chat summaries

With `clickup.summary_channel` in `stores/<id>.json` (today the operator's private
`#jarvis-drafts`) and `CLICKUP_PAT` set, the tick posts (`summaries.py`):

| When | Post |
|---|---|
| 30 min after the shift's crew window ends (`team_to`: opening 08:30, shift change 15:30, closing 22:00) | Done of total, shoutouts (ticks per person), what is still open, steps left unticked under a ticked task, done without the photo it needs |
| A closing count is sent (or sent again) | Who and when, notes, then every field under the closing form's sections |

Before cutover a shift summary waits for a ClickUp mirror run after the shift was due (the
tick starts one), at most an hour. Each post is recorded in `posts` before sending, so it is
sent once; a failed send logs `store-tasks fail reason=summary_post`.

The closing count follows the ClickUp form: its sections and order come from ClickUp's own
"Daily Closing Update" post (`admin count-sections`); fields are matched by name and that
day's values, so the two "Mango" fields (a base, a fruit) land in the right section.

## Slack

The bridge needs a Slack app in the workspace the crew uses, with a bot token
(`chat:write`, `commands`), a `/task` slash command pointing at `/slack/commands`, and a
message shortcut with callback id `make_task` pointing at `/slack/interactions`. Store its
values as secrets `store-tasks-slack-bot-token`, `store-tasks-slack-signing-secret` and
`store-tasks-slack-channels` (JSON `{"palmetto": "<channel id>"}`); the deploy workflow then
deploys the bridge. Add a Pub/Sub push subscription on `store-tasks-events` to
`<bridge url>/events?token=<store-tasks-push-token>`.

| In Slack | Result |
|---|---|
| A shift is 30 minutes past due with open tasks | One post in the store channel listing them |
| The closing count is sent | One post with the notes (out of, running low, shift notes) |
| `/task closing: wipe the front door` | A one-off task on today's closing list |
| Message → ⋯ → "Make this a task" | The message becomes a task; a reply in the thread confirms |

## Cost

Cloud Run scales to zero and bills per request; Firestore bills per document read and
write (a named database has no free quota). At one store's volume — about 100 tasks a day,
one or two iPads refreshing once a minute — that is roughly 3 million reads a month, about
$1, plus cents for storage, photos and the scheduler job. This is an estimate from list
prices, not yet a measured bill.
