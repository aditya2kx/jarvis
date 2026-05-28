-- 001_initial_schema.sql
-- Phase 3: BigQuery as canonical structured store for BHAGA.
-- Creates all tables in the bhaga dataset (jarvis-bhaga-prod).

CREATE TABLE IF NOT EXISTS bhaga.square_transactions (
  transaction_id STRING NOT NULL,
  date_local DATE NOT NULL,
  event_type STRING,
  gross_sales_cents INT64,
  discount_cents INT64,
  net_sales_cents INT64,
  tip_cents INT64,
  total_collected_cents INT64,
  net_total_cents INT64,
  source STRING,
  staff_name STRING,
  location STRING,
  created_at_src_iso STRING,
  created_at_local_iso STRING,
  scraped_at_utc TIMESTAMP
) PARTITION BY date_local;

CREATE TABLE IF NOT EXISTS bhaga.square_item_daily (
  date_local DATE NOT NULL,
  items_sold INT64,
  units_sold INT64,
  gross_sales_cents INT64,
  avg_item_price_cents INT64,
  scraped_at_utc TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bhaga.square_daily_rollup (
  date_local DATE NOT NULL,
  txn_count INT64,
  gross_sales_cents INT64,
  tip_cents INT64,
  net_sales_cents INT64,
  refund_cents INT64,
  order_count INT64,
  scraped_at_utc TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bhaga.adp_shifts (
  date DATE NOT NULL,
  employee_id STRING NOT NULL,
  canonical_name STRING,
  raw_employee_name STRING,
  in_time STRING,
  out_time STRING,
  regular_hours FLOAT64,
  ot_hours FLOAT64,
  doubletime_hours FLOAT64,
  total_hours FLOAT64,
  shift_count INT64,
  scraped_at_utc TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bhaga.adp_punches (
  date DATE NOT NULL,
  employee_id STRING NOT NULL,
  canonical_name STRING,
  raw_employee_name STRING,
  punch_index INT64,
  in_time STRING,
  out_time STRING,
  regular_hours FLOAT64,
  ot_hours FLOAT64,
  doubletime_hours FLOAT64,
  total_hours FLOAT64,
  scraped_at_utc TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bhaga.adp_wage_rates (
  employee_id STRING NOT NULL,
  canonical_name STRING,
  wage_rate_dollars FLOAT64,
  ot_rate_dollars FLOAT64,
  is_salaried BOOL,
  excluded_from_labor_pct BOOL,
  excluded_from_tip_pool BOOL,
  raw_employee_names_json STRING,
  earnings_json STRING,
  scraped_at_utc TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bhaga._schema_migrations (
  version INT64 NOT NULL,
  name STRING NOT NULL,
  applied_at TIMESTAMP NOT NULL
);
