# pup-watch

Emails when the pup is let out **alone** in the daycare yard.

He is a white English Cream Golden Retriever, 80 lb, and the daycare only ever
puts him out on his own — groups of dogs are never him. That turns "how many
dogs are in the yard" from a weak proxy into the primary signal, and it is why
this worker can be both accurate and free.

- **Camera:** public ipcamlive stream, no credentials. Source of truth for which
  yards to watch is [`cameras.json`](cameras.json).
- **Latency:** up to 60s (one Cloud Scheduler tick).
- **Cost:** $0/month inside the Cloud Run + Cloud Scheduler free tiers.
- **Deploy:** [`.github/workflows/pup-watch-deploy.yml`](../../.github/workflows/pup-watch-deploy.yml)

## Pipeline

Ordered cheapest-first, so the expensive stages almost never run.

| Stage | Cost | What it does |
|---|---|---|
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

## Configuration

Runtime knobs come from Firestore (`pup_watch/config` in the named `pupwatch`
database) and are overlaid on the defaults in `config.py`, so tuning a threshold
does not need a redeploy. Unknown or unparseable keys are ignored.

| Env var | Purpose |
|---|---|
| `PUPWATCH_NOTIFY_TO` | Comma-separated recipients. **Never** committed — personal addresses |
| `PUPWATCH_ADMIN_TOKEN` | Required; the service refuses all control endpoints without it |
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
Scheduler once a minute. An idle tick returns in about a millisecond.

Measured: one active poll takes ~10s wall time, dominated by the 8s frame-grab
window. At 8h/day for 22 days/month that is roughly 105,000 vCPU-seconds
against the 180,000 free allowance, and ~211,000 GiB-seconds against 360,000.
Free, with headroom, but not by an order of magnitude — so if session hours grow
a lot, check this before assuming it is still free.

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
- **Another cream dog let out alone.** The local stages cannot tell them apart;
  this is exactly what the Gemini re-ID stage is for.
- **Occlusion.** The gazebo, playhouse and ramps hide dogs. Mitigated by
  sampling 4 spaced frames per poll and many polls per visit.
- **Accuracy numbers above are from composites**, because the yard was empty
  when this was built. A labelled capture during real daycare hours is still
  needed to quote true precision/recall.
