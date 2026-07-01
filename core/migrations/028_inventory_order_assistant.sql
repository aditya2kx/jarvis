-- 028_inventory_order_assistant.sql
-- Analytical view for the Order Assistant table (Issue #113, slice A).
-- Computes per-base: current stock, reported time, last restock date,
-- last-7-eligible-day-OF-WEEK usage (total + avg/day + days left),
-- the specific calendar dates counted, and recent exclusion reasons.
--
-- Usage estimator: downward-only, restock-robust.
--   consumed[i] = GREATEST(prev_close - curr_close, 0)
--   Upward jumps (curr > prev + 1.0 tub) = restock; 0 contribution to usage
--   but flagged for the "last restock date" column.
--
-- Eligibility for a transition ending on date D:
--   1. Previous reading exists (not the very first day)
--   2. No submission gap: D = prev_date + 1 calendar day
--   3. Current reading >= 1 tub
--   4. Store was open: orders > 0 in vw_model_labor_daily for D
--   5. Not a restock day: curr_close - prev_close <= 1.0
--
-- Window: most recent ELIGIBLE day per WEEKDAY (Mon-Sun).
--   Deduplicates e.g. "two Fridays" when a week has a gap; each weekday
--   contributes at most once, giving a representative weekly usage sample.
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c
--   "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_inventory_order_assistant` AS
WITH base_daily AS (
  -- Latest reading per store/item/day (same dedup logic as vw_inventory_base_latest_daily)
  SELECT store, item, submitted_date, submitted_ts, quantity_units, raw_text
  FROM (
    SELECT *,
      ROW_NUMBER() OVER (
        PARTITION BY store, submitted_date, item
        ORDER BY submitted_ts DESC
      ) AS rn
    FROM `jarvis-bhaga-prod.bhaga.inventory_closing_daily`
    WHERE category = 'base' AND parse_ok = TRUE
  ) t
  WHERE rn = 1
),
transitions AS (
  -- Pair each day's reading with the previous day's reading via LAG;
  -- join to sales for store-open detection.
  SELECT
    b.store,
    b.item,
    b.submitted_date,
    b.submitted_ts,
    b.quantity_units  AS curr_close,
    LAG(b.quantity_units) OVER w AS prev_close,
    LAG(b.submitted_date)  OVER w AS prev_date,
    l.orders AS orders_on_day
  FROM base_daily b
  LEFT JOIN `jarvis-bhaga-prod.bhaga.vw_model_labor_daily` l
    ON l.date = b.submitted_date
  WINDOW w AS (PARTITION BY b.store, b.item ORDER BY b.submitted_date)
),
scored AS (
  SELECT *,
    GREATEST(prev_close - curr_close, 0.0)        AS usage_units,
    (curr_close - COALESCE(prev_close, 0) > 1.0)  AS is_restock,
    (prev_close IS NOT NULL
      AND DATE_DIFF(submitted_date, prev_date, DAY) = 1
      AND curr_close >= 1.0
      AND COALESCE(orders_on_day, 0) > 0
      AND NOT (curr_close - COALESCE(prev_close, 0) > 1.0)
    ) AS eligible
  FROM transitions
),
ranked_dow AS (
  -- Per weekday (Mon-Sun), keep only the most recent eligible transition.
  -- Prevents "two Fridays" when a gap week falls back to the prior week.
  SELECT *,
    ROW_NUMBER() OVER (
      PARTITION BY store, item, EXTRACT(DAYOFWEEK FROM submitted_date)
      ORDER BY submitted_date DESC
    ) AS dow_rn
  FROM scored
  WHERE eligible
),
last7 AS (
  -- Aggregate across up to 7 unique weekdays (most recent eligible day each)
  SELECT
    store,
    item,
    ROUND(SUM(usage_units), 2)                                AS usage_7d_total,
    ROUND(AVG(usage_units), 2)                                AS avg_daily_usage,
    STRING_AGG(
      FORMAT('%s (%.2f)', FORMAT_DATE('%m/%d %a', submitted_date), usage_units),
      ', ' ORDER BY submitted_date
    )                                                         AS days_considered
  FROM ranked_dow
  WHERE dow_rn = 1
  GROUP BY store, item
),
latest_reading AS (
  SELECT store, item,
    quantity_units  AS current_qty,
    submitted_ts    AS reported_ts,
    submitted_date  AS reported_date
  FROM (
    SELECT *,
      ROW_NUMBER() OVER (PARTITION BY store, item ORDER BY submitted_date DESC) AS rn
    FROM base_daily
  ) t
  WHERE rn = 1
),
last_restock AS (
  SELECT store, item, MAX(submitted_date) AS last_restock_date
  FROM scored
  WHERE is_restock
  GROUP BY store, item
),
excluded_recent AS (
  -- Recent transitions that were ineligible, with a human-readable reason.
  -- Capped to 30 days to keep the Notes string concise.
  SELECT store, item, submitted_date,
    CASE
      WHEN curr_close - COALESCE(prev_close, 0) > 1.0 THEN
        FORMAT('%s %s: restock (%.1f→%.1f)',
          FORMAT_DATE('%m/%d', submitted_date), FORMAT_DATE('%a', submitted_date),
          COALESCE(prev_close, 0.0), curr_close)
      WHEN DATE_DIFF(submitted_date, prev_date, DAY) != 1 THEN
        FORMAT('%s %s: gap (%d day(s) missing after %s)',
          FORMAT_DATE('%m/%d', submitted_date), FORMAT_DATE('%a', submitted_date),
          DATE_DIFF(submitted_date, prev_date, DAY) - 1,
          FORMAT_DATE('%m/%d', prev_date))
      WHEN COALESCE(orders_on_day, 0) = 0 THEN
        FORMAT('%s %s: closed',
          FORMAT_DATE('%m/%d', submitted_date), FORMAT_DATE('%a', submitted_date))
      WHEN curr_close < 1.0 THEN
        FORMAT('%s %s: qty<1 (%.2f)',
          FORMAT_DATE('%m/%d', submitted_date), FORMAT_DATE('%a', submitted_date),
          curr_close)
      ELSE NULL
    END AS excl_note
  FROM scored
  WHERE NOT eligible
    AND prev_close IS NOT NULL
    AND submitted_date >= DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL 30 DAY)
),
exclusions AS (
  SELECT store, item,
    STRING_AGG(excl_note, '; ' ORDER BY submitted_date DESC) AS excluded_days
  FROM excluded_recent
  WHERE excl_note IS NOT NULL
  GROUP BY store, item
)
SELECT
  'base'                                                              AS category,
  lr.store,
  lr.item,
  lr.current_qty,
  FORMAT_TIMESTAMP('%Y-%m-%d %H:%M', lr.reported_ts, 'America/Chicago') AS reported,
  lr.reported_date,
  rst.last_restock_date,
  l7.usage_7d_total,
  l7.avg_daily_usage,
  ROUND(
    SAFE_DIVIDE(lr.current_qty, NULLIF(l7.avg_daily_usage, 0)),
    1
  )                                                                   AS days_left,
  l7.days_considered,
  exc.excluded_days
FROM latest_reading lr
LEFT JOIN last7        l7  USING (store, item)
LEFT JOIN last_restock rst USING (store, item)
LEFT JOIN exclusions   exc USING (store, item)
ORDER BY lr.current_qty DESC;
