# Palmetto Operator Console — Resource Cost

Two buckets: **design/build-time** (what we're spending now) and **runtime** (what
the deployed site costs per month). Short answer: design-time is **~$0 net-new**,
runtime is **≈ $2–8/month** at this store's scale.

## 1. Design / build-time (now)

| Resource | Cost | Notes |
|---|---|---|
| Figma | Already paid | Professional seat you upgraded; MCP tool calls are free within the plan (200/day). No per-call charge. |
| Cursor / LLM dev tokens | Metered dev cost | Tracked in BQ `jarvis_dev` per the cost ledger; shows in the Grafana dev-cost dashboard. This is the only real spend and it's the normal per-PR review/build cost, not a new resource. |
| BigQuery reads during design | ~$0 | I read a handful of small views/migrations. On-demand BQ is $6.25/TiB scanned; these scans are kilobytes. Effectively free (well under the 1 TiB/mo free tier). |
| GitHub | $0 | Existing repo + Actions minutes already in use. |

**Net-new design cost ≈ $0.** Everything used is already provisioned.

## 2. Runtime (once deployed) — monthly estimate

Scale = one store, a handful of operators, mostly nightly-refreshed data. All
services have generous free tiers.

| Resource | Est. / month | Basis |
|---|---|---|
| **Cloud Run** (console container) | $0–2 | Scales to zero; only bills on request CPU/mem. Low operator traffic → often within free tier (180k vCPU-s, 360k GiB-s, 2M requests). |
| **BigQuery** (query on demand) | $0–1 | On-demand $6.25/TiB. Screens `SELECT * FROM vw_*` (small, capped rows) + short-TTL caching → well under 1 TiB/mo free tier. |
| **Identity-Aware Proxy (IAP)** | $0 | No charge for IAP itself. |
| **Secret Manager** | ~$0.06 | $0.06 per active secret version/mo + $0.03/10k accesses. A few secrets → pennies. |
| **Artifact Registry** (image) | ~$0.10 | $0.10/GB/mo storage; one small image. |
| **Firestore** reads (freshness) | $0 | Tiny read volume; within free tier. |
| **Gemini** (restock CSV/photo parse) | ~$0–1 | Only on manual import. Gemini Flash ~$0.075/1M input tokens; a delivery-slip image + prompt is a few cents per parse, a few imports/week. |
| **Cloud Load Balancer** (if used for IAP) | ~$18 if dedicated LB | ⚠️ The one line item that can dominate — see note. |

**Estimate: ≈ $2–8/month** *if* IAP is attached without a dedicated external HTTP
load balancer. **If a dedicated global external LB is required for IAP, add ~$18/mo**
(forwarding-rule base charge).

### IAP cost note (the one thing to decide)

IAP on Cloud Run can be enabled **directly** (Cloud Run's built-in IAP integration,
no separate LB — cheapest) or **via an external HTTPS Load Balancer** (~$18/mo base).
Recommendation: use **Cloud Run's direct IAP** to avoid the LB charge unless we later
need a custom domain + CDN. This is called out as a decision in EXECUTION M1.

## 3. What could change the number

- **More stores / more traffic** → Cloud Run + BQ scale linearly but stay small.
- **Heavier dashboards / no caching** → BQ scan cost rises; mitigated by the
  5–15 min `revalidate` cache (design principle #1).
- **Frequent photo imports** → Gemini cost rises, but it's cents-per-parse.

Bottom line: your "assuming it's 0" is right for **design-time**; runtime is a few
dollars a month, with the only meaningful lever being whether IAP needs a dedicated
load balancer.
