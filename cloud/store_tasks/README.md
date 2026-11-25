# Store Tasks

Shift checklists for the stores (opening, shift change, closing), the closing count, and
deep-clean photos. Replaces ClickUp tasks. Three deployables, one job each:

| Piece | Code | Runs as | Job |
|---|---|---|---|
| Tasks API | `cloud/store_tasks/` | Cloud Run `store-tasks-api` | The only reader and writer of task data. All rules live here. |
| Staff app | `apps/store-tasks/` | Cloud Run `store-tasks` (static files, public) | **The one link** everyone opens: "Sign in with Google", or "Set up this iPad for the shop". Talks only to the Tasks API. |
| Google sign-in hop | the same image | Cloud Run `store-tasks-app` (Google sign-in) | Only `/v1/handoff`: Google proves who you are, then the browser goes back to the one link. Its pages redirect there. |
| Old shop link | the same image | Cloud Run `store-tasks-shop` | Redirects to the one link, so a bookmarked iPad still lands there. |
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
- **One link, every kind of sign-in.** "Sign in with Google" on the one link goes to
  `/signin`, which hops through the Google-gated `store-tasks-app` (Cloud Run direct IAP, the same
  building blocks as the Operator Console: [`skills/google_signin/`](../../skills/google_signin/README.md)).
  There the API verifies Google's signed assertion, checks the account is on the store's list
  and sends the browser back with a 30-day device token in the URL fragment (never sent to a
  server); the app keeps it and it is renewed whenever the app loads. Removing the account stops
  it at the next call. The hop always uses the gate's deterministic host, never the hash host.
- **Who ticked.** A shared Google account (a store iPad) asks for a name and PIN; a person's own
  Google account goes straight in as them, no PIN. The tick records that person and the time.
  Sessions last 12 hours and a shared account locks after 3 idle minutes.
- **The shop device.** The shop iPad has no Google account. On the one link it taps "Set up this
  iPad for the shop" and enters the store's 6-digit shop code once (a manager sets it in Manage → Sign-in); it then keeps a
  device token, renewed whenever the app loads, and works as "Shop". Crew never sign in there:
  they pick who is doing each task, and a tick from the shop counts for the task's assignee.
  A manager taps Manager and enters their PIN to manage; it falls back to the shop after 3 idle
  minutes. Changing the code signs every shop device out. Ten wrong codes within 15 minutes,
  from anywhere, pause shop sign-in for 15 minutes; devices already signed in keep working.
- **One origin.** The app's nginx passes `/v1/` on to the API. On the sign-in hop it re-sends
  Google's signed assertion as `X-Signin-Assertion` (Google's front end drops `x-goog-*` headers
  between services) and the API verifies it against the hop's audience. Calling the API without
  a device token or a Google sign-in is refused.
- **Who is doing it.** Anyone signed in picks a person for a task, or for every unassigned
  task on a list at once, as in ClickUp. Only people Active in the latest ADP directory
  snapshot (`adp_directory_status`) can be picked; the hourly sync sets `adp_active` on each
  person, adds anyone employed who is missing, and changes nothing if the snapshot is empty.
  A ClickUp nickname is linked to its ADP name through the person's `adp_name`. Nothing is assigned
  automatically. The ADP schedule (copied hourly from BigQuery into `shared_shifts`, so the
  tablet never waits on the warehouse; BigQuery stays the source) only puts the people
  scheduled today at the top of the picker.
- **One task.** Opening a task shows how to do it, example photos or short videos (up to 30 MB)
  of how it should look, photos from earlier days, today's photos (any task takes photos, from a phone camera or a
  computer's files), its steps and the team's notes. A manager edits the how-to and example
  photos there; on a recurring task the edit applies every day from today. A task added for
  one day can be deleted by whoever added it or a manager.
- **Adding, repeating, copying.** One form creates, copies and changes tasks. Anyone adds a
  task for the day. A manager can make it repeat: every day, chosen weekdays every 1–12
  weeks, or a day of the month, with optional first and last dates. A day's list holds only
  what is due that day. Changing a repeating task's schedule or shift updates lists already
  built from today on (open, untouched tasks only). Duplicate on any task opens the form
  filled in from it, and a copied repeating task keeps its example photos.
- **Order of work.** Each shift lists tasks in the order the crew does them (the definition
  `order`, also applied to ClickUp-mirrored days). The starting order came from ClickUp tick
  times; a manager moves a task up or down in Manage → Tasks (`POST /v1/templates/order`), which
  keeps the previous order in the template history and re-orders lists from today on.
- **Finding a task.** "Find a task" above the list filters the shift by words in the title and
  offers a jump to other shifts that also match. A task with steps shows folded; "Show steps"
  opens it, and it opens by itself when only its steps match the search or filter.
- **Photo-needed ticks.** Ticking a task that needs a photo opens the camera; the photo is
  saved and the task ticked in one step.
- **Feedback.** One Feedback button, on the day list and in Manage: "Something's wrong" or "Idea", text and an
  optional photo. The app sends with it where the person was (screen, day, shift, open task,
  app build, device, offline queue) and their last 100 steps (screens, taps, API calls with
  status and time, JavaScript errors), kept only in the tab's session storage. It is stored in
  the `feedback` collection, logged as `store-tasks feedback id=…`, and DMed to the operator by
  the BHAGA bot (`SLACK_BOT_TOKEN` from secret `slack-bot-token`,
  `STORE_TASKS_FEEDBACK_SLACK_USER`); a failed DM logs `reason=feedback_notify` and never
  fails the send. Managers read and close it in Manage → Feedback.
- **Projects.** Apart from the stores, a project is a private list of action items for a few named
  people (`tracker.py`, collections `trackers` and `tracker_items`), at `/p/<id>` on the same link,
  created with `admin project`. A member is known only by their own Google account: they need no
  staff record, PIN or store access, and a Google account that is on a project but not a store is
  sent to its project from the front page. Someone on more than one place (their store's tasks and a
  project, or two projects) gets a workspace switcher top left on every screen (`GET /v1/projects`,
  `Projects.tsx`): a tile with the place's name; a menu on wide screens, a sheet on a phone. Everyone else, and the shop device, sees nothing of projects.
  A project is built from the same pieces as a store's day (`parts.tsx`: task row, search, All / Mine /
  Unassigned with Hide done, the side panel or sheet, done bar, who is doing it, notes; `People.tsx`
  picker, which picks several people on a project; `Photos.tsx` grid, viewer and Add photo), so both
  work alike. A task has the people doing it, store tags, an optional due date, points, photos and
  notes, an optional priority (Urgent / High / Normal / Low, ClickUp's four), and Duplicate; it
  stays open until ticked, and Start marks it In progress (`started_by`) on the way; Undo on a tick goes
  back to In progress if it had been started. The list narrows by who is doing it and by priority (`FilterSelect`),
  beside All / Mine / Unassigned, and sorts urgent first within each person's group. It can repeat with the store tasks' picker
  (`Repeat.tsx`) and schedule rules (`Tasks._schedule`, `model.due_on`): ticking a repeating task
  adds a fresh open copy (`repeat_of`) due on the schedule's next day after the later of its due
  date and today, and undoing the tick takes that copy back unless someone has noted, changed or
  photographed it. Its photos are kept under `_projects/<id>/` and
  served only to members (`GET /v1/projects/<id>/photos/<path>`), never by a store's photo route; Hide done
  starts on, as a project never starts over like a day. "Add a task" takes one task per line or pasted
  meeting notes (`tracker.parse`): a line of only names starts a group those people do, numbered or
  bulleted lines are tasks, indented lines are points; the person checks the split before anything
  is added. Report a problem works there too (`POST /v1/projects/<id>/feedback`): it lands in
  Manage → Feedback and the Slack DM of the store whose manager runs the project (`Tracker.home`). Each change to an item keeps
  what it said before (`log`), and each change to who is on it is added to the project's `history`.
  No reminders.
- **Any screen.** Phone: one column, a task opens as a sheet. iPad landscape and desktop:
  the list and the open task side by side.
- **Pull to refresh.** Pull down at the top of a list and let go: the app reloads (fresh tasks
  and any new version; queued ticks are kept). Each screen's list scrolls, not the page, so
  neither Safari nor the home-screen app has a built-in one. In the iPhone home-screen app the
  page, sheets and toasts measure how far iOS stops short of the bottom edge and make it up
  (`src/viewport.ts`, same fix as the Operator Console).
- **Offline.** The app shell and the day are kept on the device. Ticks and assignments made
  offline queue on the device and send when the connection returns; a repeated tick is a
  no-op. Photos and notes need a connection.
- **Closing count.** Same fields as the ClickUp form, including its photos. The nightly inventory job reads it
  through `closing-counts`, which returns the ClickUp form-task shape, so
  `agents/bhaga/scripts/ingest_inventory.py` parses it unchanged. Switch a store with the
  profile key `"closing_source": "store_tasks"` (default `clickup`). On a day ClickUp still runs,
  a count sent here is kept as `source: "tryout"`: shown, never returned by `closing-counts`, and
  replaced (with its photos) by the count the crew sends in ClickUp.
- **Events.** `task.completed`, `task.reopened`, `task.created`, `shift.incomplete`,
  `count.submitted` on Pub/Sub. A failed publish is logged and never fails the tick.

## Access

| Caller | Proof | Can do |
|---|---|---|
| Anyone | none | `GET /health` |
| Google account not on the list | Google sign-in | Nothing (Google refuses; the API also answers 403) |
| Shared account (store iPad) | Google sign-in → device token (`X-Shop-Device`, kind `google`) | See the store's shifts and staff names |
| Shop device | shop code → device token (`X-Shop-Device`) | Tick, assign, add photos and notes as "Shop" (no PIN); cannot add or change tasks; managers sign in on it with their PIN |
| Crew | Google sign-in + PIN session (`X-Staff-Session`) | Read days, tick, undo own ticks, assign anyone, add photos and notes, remove own photos, send the count, add a one-off task and delete their own |
| Manager | Google sign-in + 6-digit PIN session, or their own Google account | Also: how-to text and example photos, templates, people and PINs, sign-in accounts, history, undo anyone's tick, remove any photo or one-off task |
| A project member | Their own Google account (sign-in hop → device token, kind `google`); no staff record, PIN or store access | `/v1/projects*`: see, add, change, tick and comment on that project's action items. Everyone else, the shop device included, gets 404; store routes still refuse a project-only account. Only its admin changes who is on it, which also adds or removes them on the Google sign-in list |
| Scheduler, nightly job, Slack bridge | `Authorization: Bearer` internal token | `/internal/*` |

PINs and shop codes are stored as scrypt hashes; five wrong PINs lock that person for five minutes. An
account can only see its own store. Adding or removing an account in Manage → Sign-in also
adds or removes it on the app's Google sign-in list, so the two never drift.

**More than one store.** A person's own account has a home store and may also open others
(`also`: store → their staff id there, set by `admin account`, or by a manager adding the email on the Sign-in page, for an email whose
home is another store; removing it there drops only that store, and Google sign-in stays while one is left). The switcher lists each of their stores; picking one sends `X-Store` on every request and
the API treats the account as that store's (`Tasks.account_in`), so roles, PIN-free sign-in and
sessions are per store. A change queued offline in one store still lands there after a switch (its
session names the store). The shop device and shared accounts have only their own store.

## Moving off ClickUp

`cutover_date` on the store decides who owns a day: before it, days are mirrored from
ClickUp; from it on, days are built here and ClickUp is ignored.

Every mirrored task is linked to its recurring definition (`template_id`), so it shows that
definition's how-to, example media, photo rule and photos from earlier days. Today and later
ClickUp days can be **tried out** in the app: the first tick, assignment, photo or note keeps
ClickUp's state on the task (`trial`, `clickup_state`). Summaries and alerts read ClickUp's state
(`svc.clickup_view`), a ClickUp change to the task wins, and the tick's `reset_trials` puts every
tried-out task back as ClickUp has it once its day is over. Earlier days stay read-only.

ClickUp adds a recurring task's next copy only once the current one is due, so days ahead hold
few ClickUp tasks. The day list fills them from the definitions as **planned** tasks
(`source: "planned"`, `svc.planned`): computed on read, never stored, read-only, and replaced by
ClickUp's copy (matched on `template_id`, else shift and title) as soon as the mirror has it.

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
python3 -m cloud.store_tasks.admin link-templates --store palmetto   # link mirrored tasks to their definitions + earlier photos
python3 -m cloud.store_tasks.admin photo-sizes --store palmetto      # thumbnail + screen-size copies of stored photos (Cloud Run job store-tasks-photo-sizes)
```

A store with no ClickUp (Houston) skips the mirror: seed it with its `cutover_date` already set,
copy another store's task list and closing-count fields, then add people. Its shift summaries are
not posted anywhere (they go to a ClickUp chat).

```bash
python3 -m cloud.store_tasks.admin seed --store houston
python3 -m cloud.store_tasks.shared_sync --store houston                       # ADP crew from bhaga_houston
python3 -m cloud.store_tasks.admin copy-templates --from palmetto --store houston
python3 -m cloud.store_tasks.admin manager --store houston --name "First Last"  # no --pin: signs in with Google
python3 -m cloud.store_tasks.admin account --store houston --email me@example.com --staff "First Last"
```

Photos are served at `?size=thumb` (480 px) or `?size=view` (1600 px); copies live under
`<store>/_sized/<size>/` and are made on first request when missing. ClickUp's originals (~8 MB)
are only sent for downloads. Latency target: p99 ≤ 500 ms per action (`/v1/days` reads in parallel;
the API's startup probe `/health/ready` opens Firestore and Storage before an instance takes traffic).

**ClickUp webhooks** (`clickup_webhooks.py`) keep it live: the API registers one webhook per
configured list itself (every 6 hours it re-checks them and replaces any ClickUp suspended),
keeping each signing secret in the `webhooks` collection. A change in ClickUp calls
`/webhooks/clickup/<store>/<list>` (signature checked); the task's status and fields, or the
closing count, update in seconds, and its comments and files follow on the next tick (inbox).
Nothing here runs on a laptop or needs Cursor.

Every import write is keyed by the ClickUp task id, so re-running is an upsert. Until
cutover, as a net for anything a webhook missed, the scheduled tick re-mirrors the last three days every two hours: fields and status,
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

The bridge posts as **Palmetto Texas Operator** (app `A0CKMG7KKHD`) in
palmettosuperfoods.slack.com, defined by
[`cloud/store_tasks_slack/slack-app-manifest.yaml`](../store_tasks_slack/slack-app-manifest.yaml).
Change the app by editing the manifest and pushing it with `apps.manifest.update` (an app
configuration token from api.slack.com/apps); the icon is `apps/store-tasks/public/icon-512.png`,
uploaded on the app's Basic Information page. Secrets: `store-tasks-slack-bot-token`,
`store-tasks-slack-signing-secret` and `store-tasks-slack-channels` (JSON
`{"houston": "C0CLVRS97NC"}`); the deploy workflow deploys the bridge once they exist. Then add
a Pub/Sub push subscription on `store-tasks-events` to
`<bridge url>/events?token=<store-tasks-push-token>`. The Houston channels are private, so the
bot is invited (`/invite @Palmetto Texas Operator`) rather than joining. The same bot token posts
Houston's BHAGA automations (`skills/slack_chat`).

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
