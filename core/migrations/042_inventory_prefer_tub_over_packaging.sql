-- 042_inventory_prefer_tub_over_packaging.sql
-- ClickUp closing form has two custom fields both named "Mango":
--   f6bd318b-…  tub count (e.g. "16")
--   fdd1e022-…  packaging count (e.g. "9 boxes", "3 cases")
-- Both land in inventory_closing_daily (distinct field_id). Views that
-- collapse to one row per (store, date, item) via ROW_NUMBER … ORDER BY
-- submitted_ts DESC picked nondeterministically when timestamps matched,
-- so Operator Console Current Qty showed packaging (3/9) instead of tubs (16).
--
-- Fix: prefer rows whose raw_text is NOT a box/case packaging reading.
-- Packaging rows stay in the raw table for audit.
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c
--   "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_inventory_base_latest_daily` AS
SELECT * EXCEPT (rn)
FROM (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY store, submitted_date, item
      ORDER BY
        -- Prefer tub counts over packaging fields (ClickUp has two "Mango"
        -- fields: tubs vs boxes/cases). Equal submitted_ts otherwise made
        -- the pick nondeterministic — Jul 16/17 Current Qty showed cases.
        CASE
          WHEN REGEXP_CONTAINS(
            LOWER(IFNULL(raw_text, '')),
            r'\b(box|boxes|case|cases)\b'
          ) THEN 1
          ELSE 0
        END ASC,
        submitted_ts DESC,
        field_id ASC
    ) AS rn
  FROM `jarvis-bhaga-prod.bhaga.inventory_closing_daily`
  WHERE category = 'base'
) t
WHERE rn = 1;

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_inventory_order_assistant` AS
WITH base_daily AS (
  -- Latest reading per store/item/day (same dedup logic as vw_inventory_base_latest_daily)
  SELECT store, item, submitted_date, submitted_ts, quantity_units, raw_text
  FROM (
    SELECT *,
      ROW_NUMBER() OVER (
        PARTITION BY store, submitted_date, item
        ORDER BY
        -- Prefer tub counts over packaging fields (ClickUp has two "Mango"
        -- fields: tubs vs boxes/cases). Equal submitted_ts otherwise made
        -- the pick nondeterministic — Jul 16/17 Current Qty showed cases.
        CASE
          WHEN REGEXP_CONTAINS(
            LOWER(IFNULL(raw_text, '')),
            r'\b(box|boxes|case|cases)\b'
          ) THEN 1
          ELSE 0
        END ASC,
        submitted_ts DESC,
        field_id ASC
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
-- ── Per-item low-usage filter over trailing 30-day eligible window ──────────
elig_recent AS (
  SELECT * FROM scored
  WHERE eligible
    AND submitted_date >= DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL 30 DAY)
),
nonzero_stats AS (
  -- Median computed from NONZERO eligible days only, so zero days don't drag
  -- the reference value down (which would make near-zero noise look "normal").
  SELECT DISTINCT store, item,
    PERCENTILE_CONT(usage_units, 0.5) OVER (PARTITION BY store, item) AS med_nonzero
  FROM elig_recent
  WHERE usage_units > 0
),
scored_clean AS (
  SELECT e.*, s.med_nonzero,
    (e.usage_units = 0)                                                    AS is_zero_usage,
    (e.usage_units > 0 AND s.med_nonzero IS NOT NULL
      AND e.usage_units < 0.20 * s.med_nonzero)                            AS is_low_outlier
  FROM elig_recent e
  LEFT JOIN nonzero_stats s USING (store, item)
),
-- ── High-side robust-z over the low-filtered survivors ──────────────────────
hi_med AS (
  SELECT *,
    PERCENTILE_CONT(usage_units, 0.5) OVER (PARTITION BY store, item) AS med_surv
  FROM scored_clean
  WHERE NOT is_zero_usage AND NOT is_low_outlier
),
hi_mad AS (
  SELECT *,
    PERCENTILE_CONT(ABS(usage_units - med_surv), 0.5)
      OVER (PARTITION BY store, item) AS mad_surv
  FROM hi_med
),
hi_scored AS (
  SELECT *,
    (mad_surv > 0
     AND SAFE_DIVIDE(usage_units - med_surv, 1.4826 * mad_surv) > 2.5) AS is_high_outlier
  FROM hi_mad
),
ranked_dow AS (
  -- Per weekday (Mon-Sun), keep only the most recent day surviving both filters.
  -- Prevents "two Fridays" and drops zero/near-zero lows + robust-z high spikes.
  SELECT *,
    ROW_NUMBER() OVER (
      PARTITION BY store, item, EXTRACT(DAYOFWEEK FROM submitted_date)
      ORDER BY submitted_date DESC
    ) AS dow_rn
  FROM hi_scored
  WHERE NOT is_high_outlier
),
last7 AS (
  -- Aggregate across up to 7 unique weekdays (most recent eligible non-outlier day each)
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
low_usage_recent AS (
  -- Eligible transitions dropped by the low-usage filter (zero or < 20% of
  -- the item's nonzero median), within the same 30-day window they're computed over.
  SELECT store, item, submitted_date,
    CASE
      WHEN is_zero_usage THEN
        FORMAT('%s %s: zero usage (likely reporting gap)',
          FORMAT_DATE('%m/%d', submitted_date), FORMAT_DATE('%a', submitted_date))
      ELSE
        FORMAT('%s %s: low outlier (%.2f vs med %.2f, <20%%)',
          FORMAT_DATE('%m/%d', submitted_date), FORMAT_DATE('%a', submitted_date),
          usage_units, med_nonzero)
    END AS excl_note
  FROM scored_clean
  WHERE (is_zero_usage OR is_low_outlier)
),
high_usage_recent AS (
  -- Survivors of the low filter dropped as high-side robust-z outliers.
  SELECT store, item, submitted_date,
    FORMAT('%s %s: high outlier (%.2f vs med %.2f)',
      FORMAT_DATE('%m/%d', submitted_date), FORMAT_DATE('%a', submitted_date),
      usage_units, med_surv) AS excl_note
  FROM hi_scored
  WHERE is_high_outlier
),
exclusions AS (
  SELECT store, item,
    STRING_AGG(excl_note, '; ' ORDER BY submitted_date DESC) AS excluded_days
  FROM (
    SELECT store, item, submitted_date, excl_note FROM excluded_recent WHERE excl_note IS NOT NULL
    UNION ALL
    SELECT store, item, submitted_date, excl_note FROM low_usage_recent
    UNION ALL
    SELECT store, item, submitted_date, excl_note FROM high_usage_recent
  ) combined
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
