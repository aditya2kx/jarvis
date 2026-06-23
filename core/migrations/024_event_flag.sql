-- Migration 024: Add event_flag to model_labor_daily.
--
-- An additive nullable BOOL column, defaulting to FALSE via COALESCE in all
-- consumers.  Serves as the extensible exogenous hook for manually marking
-- known demand drivers (promos, store events, competitor openings, holidays)
-- that the adaptive_dow_ets_v1 weather correction layer can use as a feature.
--
-- Populated manually by the operator via the store_config pattern or a
-- direct BigQuery INSERT.  Default: NULL (treated as FALSE by all readers).
--
-- Applied via: python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

ALTER TABLE `jarvis-bhaga-prod.bhaga.model_labor_daily`
  ADD COLUMN IF NOT EXISTS event_flag BOOL OPTIONS(description="Manually set to TRUE for known demand-driver events (promos, holidays, competitor openings). Used as an exogenous feature in adaptive_dow_ets_v1.");
