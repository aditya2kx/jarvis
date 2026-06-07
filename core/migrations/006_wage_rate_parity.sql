-- Migration 006: add multi_rate column to adp_wage_rates for lossless
-- Sheet ↔ BQ round-trip.
--
-- The Sheet header (schema.py) has multi_rate but the initial BQ schema
-- (001_initial_schema.sql) omitted it. All other Sheet-only fields
-- (rate_history_json ↔ earnings_json; employee_name ↔ canonical_name)
-- are intentional renames handled by map_adp_wage_rate — no further
-- column additions needed.
--
-- This is the only migration needed for full raw-tab parity.

ALTER TABLE `jarvis-bhaga-prod.bhaga.adp_wage_rates`
  ADD COLUMN IF NOT EXISTS multi_rate BOOL;
