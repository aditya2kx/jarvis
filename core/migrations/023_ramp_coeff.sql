-- Migration 023: Ridge coefficient history for the ramp-aware forecast model.
--
-- Stores one row per (make_date, feature_name): the Ridge β coefficient from the
-- nightly model fit.  Enables the "feature importance evolving over time" chart in
-- Grafana Section 7A (panel 87) — each feature is a separate line showing how the
-- model's weight on that feature changes as the training window grows.
--
-- Applied via: python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.model_ramp_coeff_daily` (
  make_date               DATE      NOT NULL,
  feature_name            STRING    NOT NULL,
  coefficient             FLOAT64,
  n_train                 INT64,
  materialized_at_utc     TIMESTAMP
);
