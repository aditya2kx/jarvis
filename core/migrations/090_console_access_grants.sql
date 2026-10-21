-- 090: Operator Console per-person page/section access (owner-managed from /access)
-- Append-only: each save inserts version = previous + 1 for that email; the newest
-- version is live, older ones are the change history (never updated or deleted).
-- status 'active' | 'revoked'. grant_json = {"full": bool, "pages": {"<page>": "all" | ["<section>", ...]}}
-- with page/section ids from apps/operator-console/lib/access/registry.ts.
-- Owners (CONSOLE_OWNER_EMAILS) never have rows — they always have full access.
-- Previews write to their own copy, console_access_grants_<tag> (operator-console-preview.yml).
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c \
--   "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.console_access_grants` (
  email       STRING NOT NULL,
  version     INT64 NOT NULL,
  status      STRING NOT NULL,
  grant_json  STRING NOT NULL,
  note        STRING,
  created_by  STRING NOT NULL,
  created_at  TIMESTAMP NOT NULL
);
