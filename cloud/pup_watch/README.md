# pup-watch

Emails when the pup is let out **alone** in the daycare yard.

He is a white English Cream Golden Retriever, 80 lb, and the daycare only ever
puts him out on his own — groups of dogs are never him. That turns "how many
dogs are in the yard" from a weak proxy into the primary signal, and it is why
this worker can be both accurate and free.

- **Camera:** public ipcamlive stream, no credentials. Source of truth for which
  yards to watch is [`cameras.json`](cameras.json).
- **Latency:** up to 60s (one Cloud Scheduler tick).
- **Control:** reply `start`, `start 4h`, `stop` or `status` to any pup-watch
  email — see [Controlling it by email](#controlling-it-by-email).
- **Cost:** $0/month inside the Cloud Run + Cloud Scheduler free tiers, now at
  roughly 16% of them rather than ~0: the mailbox poll makes every idle tick
  cost ~1s of CPU instead of ~1ms (900 ticks/day × ~1.05s ≈ 28k of the 180k
  free vCPU-seconds/month, measured 2026-09-16).
- **Deploy:** [`.github/workflows/pup-watch-deploy.yml`](../../.github/workflows/pup-watch-deploy.yml)

## Pipeline

Ordered cheapest-first, so the expensive stages almost never run.

| Stage | Cost | What it does |
|---|---|---|
| Control poll (`control.py`) | ~1s, every tick | Reads the mailbox for a start/stop reply |
| Session check | ~1ms | Returns immediately unless monitoring is on |
| Frame grab (`stream.py`) | ~8s wall | 4 frames, 2s apart, via ffmpeg from the HLS playlist |
| Detection (`vision.py`) | ~270ms × 3 per frame | Full frame + 2 yard tiles; counts dogs and people |
| Cream gate (`vision.py`) | ~1ms | Is the dog's box bright and desaturated? |
| Identity (`identify.py`) | 1 API call | Gemini re-ID against reference photos — candidates only |
| Episode (`episode.py`) | ~0 | One email per visit, not per poll |
| Email (`notify.py`) | 1 API call | Both recipients, annotated frame attached |

Two rules are hard vetoes rather than score adjustments:

- **More than one dog ⇒ not him.** He is only ever out alone.
- **People do not matter.** Staff in the yard is normal and never suppresses an alert.

And one deliberate fail-open: if the identity check is *inconclusive* (no API
key, no reference photos, API error) the sighting still goes through. Only a
*confident* "different dog" vetoes it. Silently swallowing real sightings
because a dependency is unconfigured is the worse failure here.

## Why these thresholds

Measured on real frames from this camera, not guessed. The pup was composited
into a real (empty) yard frame at 12 position/size combinations, with
camera-grade JPEG compression applied.

| Finding | Number |
|---|---|
| Pup's height in this yard | ~55px at the far fence, ~110px in the foreground |
| Detector recall, full frame only | unreliable below ~70px |
| Detector recall, **full frame + 2 tiles** | **12/12 at ≥55px** |
| Cream fraction on true positives | 48–60% (threshold: 30%) |
| Dogs found on the real empty yard | **0** at any confidence; best person score 0.018 |
| 9MB nano detector | failed exactly in the 55–70px band → rejected |

So `dog_min_box_px=40`, `dog_score_min=0.25`, and
`cream_pixel_fraction_min=0.30` all sit comfortably between the measured
true-positive and false-positive populations.

Tiling is the single most important choice here: it roughly doubles the pup's
apparent size in at least one pass, and it is what lifted far-field recall.
`vision.dedupe` then collapses the same dog seen in two tiles, so tiling cannot
inflate the dog count and trip the multi-dog veto.

**Recall over a visit, not a frame.** Even if per-frame recall were only 60% at
his worst position, two agreeing frames are required per poll and he is out for
many minutes across many polls, so the chance of missing an entire visit is
negligible. This is why `min_hits_per_poll=2` costs nothing in practice while
killing single-frame flukes.

## Operating it

Start and stop are HTTP + admin token, the same shape as
`tesla-aladdin-garage`'s `X-Garage-Token`. The token lives in Secret Manager
(`pupwatch-admin-token`).

```bash
SERVICE=$(gcloud run services describe pup-watch --region us-central1 \
  --project jarvis-bhaga-prod --format='value(status.url)')
TOKEN=$(gcloud secrets versions access latest --secret=pupwatch-admin-token \
  --project jarvis-bhaga-prod)

# Start monitoring (defaults to the 12h ceiling; pass hours to shorten)
curl -sS -X POST "$SERVICE/session/start" \
  -H "X-PupWatch-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"hours": 9}'

# Where things stand
curl -sS "$SERVICE/session" -H "X-PupWatch-Token: $TOKEN"

# Stop
curl -sS -X POST "$SERVICE/session/stop" -H "X-PupWatch-Token: $TOKEN"

# Unauthenticated liveness
curl -sS "$SERVICE/health"
```

A session auto-expires at `stop_after_ts`, and in any case after
`session_max_hours`, so a forgotten session stops polling instead of running
until the end of time.

## Controlling it by email

curl-with-a-token is not usable from a phone at daycare drop-off, so every tick
also reads the notification mailbox and acts on a reply. **Reply to any
pup-watch email** — a sighting or an earlier acknowledgement — with one of:

| Reply | Effect |
|---|---|
| `start` | **Keep monitoring until an explicit `stop`.** No end time |
| `start 4h` | Monitor for 4 hours, then stop on its own (`4`, `4h`, `4 hours`, `1.5hr` all parse, capped at `session_max_hours`) |
| `stop` | Stop monitoring |
| `status` | Whether monitoring is on, until when, and when it last alerted |

A bare `start` is open-ended because the operator does not know in advance when
the pup comes home. `session_max_hours` bounds only *fixed-length* sessions; an
open-ended one is bounded instead by `session_absolute_max_hours` (default 7
days), which exists purely so a session nobody stops cannot poll forever. If it
ever fires it **emails** — as does *every* automatic stop, without exception:
monitoring once expired itself at 6am in silence and the operator only found out
a day later, after his pup had been out in an unwatched yard. A watcher that has
stopped looks exactly like one that is working and seeing nothing, so a stop it
decides on its own is only safe if it is announced. Note that a duration given *wrongly* (`start soon`) falls back to the
bounded ceiling rather than becoming open-ended: fumbling the syntax should not
be rewarded with an unbounded session.

The command must be the **first line** you type. A chatty reply like "stopped
raining, he loved it" is deliberately not a command. `pup stop` also works, so a
fresh email (not a reply) can bootstrap the very first session before any
sighting mail exists. Every accepted command is acknowledged by email to **both**
recipients, so one person cannot silently switch monitoring off for the other.

Three gates must all pass, because "anyone who learns the address can switch off
the dog camera" is not an acceptable failure mode:

1. **Sender is in `PUPWATCH_NOTIFY_TO`.** Nobody else can command it.
2. **The message is provably from that person.** Either it carries the `SENT`
   label — only the account holder can produce one — or Gmail recorded
   `spf=pass` *and* `dkim=pass`. A forged `From` alone gets nothing: Gmail
   writes `Authentication-Results` itself on delivery.
3. **The command parses strictly**, as the first unquoted line.

Consumption is idempotent via a hidden Gmail label we own (`pupwatch-handled`),
so a command fires exactly once even if the acknowledgement email fails.

That label replaced an earlier design that keyed off the **unread** flag, which
did not work at all and is worth understanding before anyone "simplifies" it
back: **Gmail marks a message you compose yourself as already read.** The
operator replies from the same mailbox the alerts are sent from, so his replies
arrive with no `UNREAD` label — `is:unread` never matched them, and every command
he sent was ignored with no ack and not even a rejection in the logs. Mail sent
through the API *does* arrive unread, which is exactly why the pre-merge evidence
passed while the real thing was broken. Read state is a property of who sent the
message and how; a label we set ourselves is not.

Commands older than `control_max_age_minutes` (default 30) are discarded
unactioned, so a reply found after an outage cannot start monitoring hours later.

The one asymmetry worth knowing: **`start` must work when nothing is running**,
which is why the mailbox is polled *before* the session check and therefore on
every idle tick. That is the entire reason idle ticks now cost ~1s instead of
~1ms (see [Cost model](#cost-model)). Set `control_email_enabled: false` in the
Firestore config to turn the whole path off without a redeploy.

## Configuration

Runtime knobs come from Firestore (`pup_watch/config` in the named `pupwatch`
database) and are overlaid on the defaults in `config.py`, so tuning a threshold
does not need a redeploy. Unknown or unparseable keys are ignored.

| Env var | Purpose |
|---|---|
| `PUPWATCH_NOTIFY_TO` | Comma-separated recipients. **Never** committed — personal addresses |
| `PUPWATCH_ADMIN_TOKEN` | Required; the service refuses all control endpoints without it |
| `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` / `GMAIL_REFRESH_TOKEN` | Sends alerts **and** reads start/stop replies. Needs `gmail.modify`, not just send: marking a command read is what makes it fire once |
| `PUPWATCH_GEMINI_TOKEN` | Identity confirmation. Use a **paid** key: free-tier prompts are used for training, and these frames contain other people's dogs and daycare staff |
| `PUPWATCH_REFERENCE_URIS` | Comma-separated `gs://` (or local) reference photos of the pup |
| `PUPWATCH_PERSIST` | `1` to use Firestore; otherwise state is a no-op |
| `PUPWATCH_MODEL_PATH` | Detector weights, baked into the image at `/app/models/detector.onnx` |

Reference photos live in GCS rather than git: they are personal photos, and
adding more should not require a redeploy.

## Cost model

The design decision that dominates cost is **not** the ML — it is whether a
container sits warm. Unlike `tesla-aladdin-garage` (`--min-instances 1`,
`--no-cpu-throttling`), pup-watch scales to zero and is woken by Cloud
Scheduler once a minute.

An idle tick used to return in about a millisecond. It now costs **~1.05s**
(measured 2026-09-16), because email control means checking the mailbox even
while monitoring is off — a token refresh plus a `messages.list`. At the
scheduler's 900 ticks/day that is ~28,000 of the 180,000 free vCPU-seconds per
month, and ~57,000 of the 360,000 free GiB-seconds: about 16% of the free tier
consumed doing nothing. That is a deliberate trade — being able to text the
system from the daycare parking lot is worth more than the headroom — but it is
the number to revisit first if cost ever bites, by polling the mailbox on every
Nth idle tick instead of every one.

Measured: one active poll takes ~10s wall time, dominated by the 8s frame-grab
window. At 8h/day for 22 days/month that is roughly 105,000 vCPU-seconds
against the 180,000 free allowance, and ~211,000 GiB-seconds against 360,000.
**An open-ended session left running is the case that breaks this**: polling the
full 15h window every day is ~9,000 vCPU-seconds/day, i.e. past the free
allowance in under three weeks. That is the trade for `start` meaning "until I
say stop", and `session_absolute_max_hours` is the backstop rather than the plan.
Adding the idle-tick control poll on top leaves the total inside the free tier
but no longer with comfortable room — so if session hours grow a lot, check this
before assuming it is still free.

Pulling video is **ingress**, which Google does not bill, so stream volume is
free regardless.

Deliberately *not* implemented: an occupancy/motion pre-gate to skip inference
when the yard looks unchanged. It would cut CPU substantially, but the simple
version does not currently fail on cost, and a motion gate risks missing a dog
lying still in the sun. It is the first lever to pull if the free tier gets
tight.

## Known limits

- **Night / IR.** In IR the image is grayscale and the cream gate stops meaning
  anything. Daycare hours are daytime, so this is accepted rather than solved.
- **Another cream dog let out alone.** The local stages cannot tell them apart,
  and — measured 2026-09-16 against a different English cream Golden composited
  into a real yard frame at 110px — **neither can the Gemini re-ID stage**: it
  returned `is_pup=True` at 0.99 confidence for the wrong dog, citing coat, ear
  shape and build. At camera-crop resolution it confirms "a cream Golden", not
  "*this* cream Golden". Because the stage fails open, this can only cause a
  false alert, never a missed one. Treat the lone-dog veto as the real signal
  until re-ID is either fed higher-resolution crops or given a discriminating
  cue (his collar/harness is currently a confound, not a help — Gemini cited a
  "similar harness" when matching the wrong dog).
- **Occlusion.** The gazebo, playhouse and ramps hide dogs. Mitigated by
  sampling 4 spaced frames per poll and many polls per visit.
- **Accuracy numbers above are from composites**, because the yard was empty
  when this was built. A labelled capture during real daycare hours is still
  needed to quote true precision/recall.
- **Email control is proven for the owning mailbox only.** Commands sent from
  the notification mailbox to itself were verified end-to-end on 2026-09-16
  (`start 3h` and `stop`, real Gmail, real Firestore). A reply from the *second*
  recipient takes the SPF/DKIM branch instead of the `SENT` branch; Gmail-to-Gmail
  mail normally passes both, but that has not been observed here yet. If her
  replies are ignored, the log line is
  `pup-watch fail reason=control_email_unauthenticated`, and the escape hatch is
  `control_require_email_auth: false` in the Firestore config — which drops the
  guarantee back to "allowlisted `From`", so prefer diagnosing the header.
- **Acknowledgement and sighting mail stays unread by design.** Our own mail is
  labelled `pupwatch-handled` so it drops out of the next query, but its read
  state is never touched: the unread badge *is* the notification, and clearing it
  would hide the alert on the phone it was just sent to.
- **An open-ended session is not free.** Continuous monitoring means an active
  poll (~10s) every minute of the scheduler window: ~900 polls/day ≈ 9,000
  vCPU-seconds/day, which passes 180,000 free vCPU-seconds/month in under three
  weeks. Fine for a boarding stay; not something to leave on permanently. `stop`
  when he is home, and `session_absolute_max_hours` is the backstop.
