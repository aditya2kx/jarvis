-- 007_store_config.sql
-- Operator-editable tunables stored in BQ (the single source of truth for config).
-- Replaces the Sheet config tab as the authoritative read source. The Sheet config
-- tab becomes a read-only projection of this table.
--
-- Edit surface: /bhaga-cloud config set <key> <value>  (Slack slash command)
--               core.store_config.set_config(...)       (pipeline / one-off scripts)
--
-- Apply: python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.store_config` (
  store       STRING    NOT NULL,
  key         STRING    NOT NULL,
  value       STRING    NOT NULL,
  notes       STRING,
  updated_at  TIMESTAMP,
  updated_by  STRING
);
