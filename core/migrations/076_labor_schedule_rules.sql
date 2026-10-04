-- 076: Operator Console scheduling rules — Issue #337
-- Append-only: every save inserts a new version; the latest row per store is
-- the live rule set and every earlier row is the superseded history (who/when).
-- rules_json is the console's ScheduleRules shape
-- ({dayRules: DayRule[], staffRules: StaffRule[]}, see
-- apps/operator-console/lib/labor/schedule-inputs.ts).
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c \
--   "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.labor_schedule_rules` (
  store       STRING NOT NULL,
  version     INT64 NOT NULL,
  rules_json  STRING NOT NULL,
  note        STRING,
  created_by  STRING NOT NULL,
  created_at  TIMESTAMP NOT NULL
);
