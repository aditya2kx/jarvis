-- Migration 021: weather_daily table for Open-Meteo ingestion.
--
-- Stores both historical ERA5 actuals (kind='actual') and NWP forward-forecast
-- rows (kind='forecast').  The nightly pipeline writes actuals for past dates
-- and forecast rows for the next 10 days; once a day passes its forecast row
-- is overwritten with the actual on the next nightly run (MERGE on date).
--
-- Metric units: temperature in °C, precipitation in mm.
-- The ramp forecast module converts to °F/inch internally for the weather
-- feature thresholds (heat_flag>90°F, rainy_flag>0.25in) derived from the
-- analysis spike.

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.weather_daily` (
  date           DATE      NOT NULL,
  tmean_c        FLOAT64,
  tmax_c         FLOAT64,
  tmin_c         FLOAT64,
  precip_mm      FLOAT64,
  is_rainy       BOOL,
  kind           STRING,   -- 'actual' | 'forecast'
  source         STRING,   -- 'open_meteo'
  fetched_at     TIMESTAMP
);
