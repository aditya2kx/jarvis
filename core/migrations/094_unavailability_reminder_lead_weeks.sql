-- 094_unavailability_reminder_lead_weeks.sql
-- Issue #435: the schedule moves from 1-2 to 3-4 weeks published; each Friday adds
-- the lead_weeks-th week out. The reminder states that horizon, its Thursday
-- follow-up tags people with a blank week in it, and drafts go to the operator's
-- private ClickUp channel through the web app so mentions are real chips.
-- Texts are replaced only where they still equal the Issue #381 defaults, so an
-- operator edit is kept.
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

ALTER TABLE `jarvis-bhaga-prod.bhaga.automations`
  ADD COLUMN IF NOT EXISTS lead_weeks INT64;

UPDATE `jarvis-bhaga-prod.bhaga.automations`
SET lead_weeks = 4,
    destination = 'drafts',
    channel_id = '8cr6661-7217'
WHERE automation_id = 'unavailability-reminder' AND lead_weeks IS NULL;

UPDATE `jarvis-bhaga-prod.bhaga.automations`
SET template = '@everyone Friendly reminder to update unavailability on ADP. Schedules through {through_date} will be published this {publish_day} based entirely off your ADP unavailability. Please keep it updated at least {lead_weeks} weeks in advance to avoid swapping shifts after that.'
WHERE automation_id = 'unavailability-reminder'
  AND template = '@everyone Friendly reminder to update unavailability on ADP. Schedule for {target_week}, will be published this {publish_day} based entirely off your ADP unavailability. Please update it beforehand to avoid swapping shifts after that.';

UPDATE `jarvis-bhaga-prod.bhaga.automations`
SET followup_template = '{flagged} One last reminder before shifts through {through_date} get published tomorrow. Please keep your ADP unavailability updated at least {lead_weeks} weeks ahead.'
WHERE automation_id = 'unavailability-reminder'
  AND (followup_template IS NULL OR followup_template = 'One last reminder before shifts get published tomorrow');
