-- 088_automations_followup_template.sql
-- Issue #381: the unavailability reminder sends a full reminder on its first
-- configured day and a short follow-up on later days; the follow-up text is
-- operator-editable alongside `template`. NULL for automations without one.
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

ALTER TABLE `jarvis-bhaga-prod.bhaga.automations`
  ADD COLUMN IF NOT EXISTS followup_template STRING;
