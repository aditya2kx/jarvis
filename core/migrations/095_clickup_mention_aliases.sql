-- 095_clickup_mention_aliases.sql
-- Map payroll canonical names → ClickUp user_id for reminder @mentions when
-- fuzzy username match fails (e.g. Huynh, Hillary → Linhchi Huynh). Issue #447.
-- Runtime edits: `/bhaga-cloud clickup-mention set "Last, First" <user_id>`.
-- Apply: BHAGA_DATASTORE=bigquery python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.clickup_mention_aliases` (
  store            STRING    NOT NULL,
  canonical_name   STRING    NOT NULL,
  clickup_user_id  STRING    NOT NULL,
  notes            STRING,
  updated_at       TIMESTAMP,
  updated_by       STRING
);

-- Seed the known Directory↔ClickUp mismatch from the #435 Thu follow-up.
MERGE `jarvis-bhaga-prod.bhaga.clickup_mention_aliases` T
USING (
  SELECT
    'palmetto' AS store,
    'Huynh, Hillary' AS canonical_name,
    '101332333' AS clickup_user_id,
    'ClickUp username Linhchi Huynh; payroll Hillary (#447)' AS notes
) S
ON T.store = S.store AND T.canonical_name = S.canonical_name
WHEN NOT MATCHED THEN
  INSERT (store, canonical_name, clickup_user_id, notes, updated_at, updated_by)
  VALUES (S.store, S.canonical_name, S.clickup_user_id, S.notes, CURRENT_TIMESTAMP(), 'migration-095');
