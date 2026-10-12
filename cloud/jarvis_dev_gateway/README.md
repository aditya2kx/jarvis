# jarvis-dev gateway

Start and check Jarvis development tasks from **Siri**, **Grok** or any HTTP client — no laptop,
no terminal. A task is a GitHub tracking issue (`[work] …`, label `jarvis-work`) plus an intake
comment posted as `jarvis-agent-bot328`, served by `jarvis-dev-signals.yml` exactly like a typed one:
`/jarvis-cloud-task …` (default) starts a **Cursor Cloud Agent**; `"where": "mac"` posts
`/jarvis-new-task …` instead, which the Mac listener picks up as always. The gateway holds no
Cursor key and never touches prod data.

| Route | Auth | Does |
|---|---|---|
| `GET /health` | none | wiring check (`github_token`, `gateway_token` booleans) |
| `POST /task` `{"text": "…", "where": "cloud"\|"mac"}` (JSON or form) | Bearer | opens the issue + intake comment; returns `{issue, url, where, speech}` |
| `GET /status` | Bearer | newest open `jarvis-work` issues, their `stage:*`, Cursor agent link; `speech` summary |
| `POST /mcp` | Bearer | MCP (Streamable HTTP, stateless JSON): tools `start_task`, `task_status` |
| `POST /mcp/<token>` | token in path | same, for connectors that only take a URL (grok.com) |

Deploy: `.github/workflows/jarvis-dev-gateway-deploy.yml` on push to `main` — Cloud Run
`jarvis-dev-gateway` (us-central1, scale to zero, max 1, public ingress + app bearer because Siri and
Grok cannot do Google sign-in). Secrets: `jarvis-dev-gateway-token` (the bearer) and
`github-bot-pat` (synced from repo secret `ADMIN_PAT` on every deploy — after a bot PAT rotation,
re-run the workflow).

## One-time setup (Cloud Shell, operator)

The deploy identity (`bhaga-orchestrator`) has no project-level Secret Manager admin, and should not
get it. `scripts/provision_cloud_agent_identity.sh` creates both secrets once (idempotent), lets the
deploy identity add versions to `github-bot-pat` only, and grants Cursor Cloud Agents read access to
that secret ([gcp-access.md](../../docs/contributing/gcp-access.md) recipe F):

```bash
bash scripts/provision_cloud_agent_identity.sh
```

The PR `self-test` job's "Probe deploy-identity IAM" step prints `deploy identity: ready` once this is
done; re-run the workflow (Actions → jarvis-dev gateway → Run workflow) to deploy.

## Get the URL and token (phone, once)

1. URL: Cloud Console → Cloud Run → `jarvis-dev-gateway` (or the deploy run's summary).
2. Token: Cloud Console → Security → Secret Manager → `jarvis-dev-gateway-token` → latest version →
   *View secret value*. Store it in 1Password; paste it only into the Shortcut / connector below.

## Siri (iOS Shortcuts)

**"Jarvis task"** — *Dictate Text* → *Get Contents of URL*:
`https://<gateway>/task`, Method **POST**, Headers `Authorization: Bearer <token>`,
Request Body **JSON** `text` = *Dictated Text* → *Get Dictionary Value* `speech` → *Speak Text*.

**"Jarvis status"** — *Get Contents of URL* `https://<gateway>/status` (GET, same header) →
*Get Dictionary Value* `speech` → *Speak Text*.

"Hey Siri, Jarvis task" → speak the requirement → Siri reads back the issue number. The Cursor agent
link lands on the issue within a minute; open it in Cursor mobile to jam. For a task you want to
pick up on the Mac instead, duplicate the Shortcut with `where` = `mac` in the JSON body.

## Grok

| Surface | Setup | Auth |
|---|---|---|
| grok.com / Grok app connectors | Connectors → New Connector → Custom → `https://<gateway>/mcp/<token>` | token in URL (no header field; OAuth only otherwise) |
| Grok CLI | `grok mcp add --transport http jarvis https://<gateway>/mcp --header "Authorization: Bearer ${JARVIS_DEV_GATEWAY_TOKEN}"` | Bearer |
| xAI API (`/v1/responses`) | `{"type":"mcp","server_url":"https://<gateway>/mcp","server_label":"jarvis","authorization":"<token>"}` | Bearer |

Then: "Use jarvis to start a task: add a last-refreshed chip to the inventory page" /
"What's the status of my Jarvis tasks?"

The path-token URL is a capability: anyone holding it can open issues (and so start paid agents).
It appears in Cloud Run request logs (project-private). Rotate by adding a new secret version and
re-running the deploy workflow.

## Verify

```bash
python3 -m pytest -q cloud/jarvis_dev_gateway
curl -fsS https://<gateway>/health
curl -fsS -H "Authorization: Bearer $T" https://<gateway>/status
```

PRs touching this directory run the container on the Actions runner against live GitHub (read-only
calls) and probe the deploy identity's IAM (`self-test` job).
