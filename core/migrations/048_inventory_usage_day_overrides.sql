-- 048_inventory_usage_day_overrides.sql
-- Issue #194: per-(store, item, date) force_include / force_exclude overrides
-- for Order Assistant usage eligibility, plus a day-grain audit view for the
-- Operator Console (layout A: one row per date with per-base Δ / status / why).
--
-- Semantics:
--   force_include → day enters elig_recent / median-MAD pools (learning)
--   force_exclude → day stays out of pools and out of avg window
--   sticky to (store, item, submitted_date) until cleared
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c
--   "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.inventory_usage_day_overrides` (
  store          STRING    NOT NULL,
  item           STRING    NOT NULL,
  submitted_date DATE      NOT NULL,
  mode           STRING    NOT NULL,  -- 'force_include' | 'force_exclude'
  note           STRING,
  updated_by     STRING,
  updated_at     TIMESTAMP
);

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_inventory_order_assistant` AS
WITH base_daily AS (
  SELECT store, item, submitted_date, submitted_ts, quantity_units, raw_text
  FROM (
    SELECT *,
      ROW_NUMBER() OVER (
        PARTITION BY store, submitted_date, item
        ORDER BY
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
scored_raw AS (
  SELECT *,
    GREATEST(prev_close - curr_close, 0.0)        AS usage_units,
    (curr_close - COALESCE(prev_close, 0) > 1.0)  AS is_restock,
    (prev_close IS NOT NULL
      AND DATE_DIFF(submitted_date, prev_date, DAY) = 1
      AND curr_close >= 1.0
      AND COALESCE(orders_on_day, 0) > 0
      AND NOT (curr_close - COALESCE(prev_close, 0) > 1.0)
    ) AS rule_eligible
  FROM transitions
),
scored AS (
  SELECT
    r.*,
    o.mode AS override_mode,
    (
      (r.rule_eligible OR o.mode = 'force_include')
      AND IFNULL(o.mode, '') != 'force_exclude'
    ) AS eligible
  FROM scored_raw r
  LEFT JOIN `jarvis-bhaga-prod.bhaga.inventory_usage_day_overrides` o
    ON o.store = r.store
   AND o.item = r.item
   AND o.submitted_date = r.submitted_date
),
elig_recent AS (
  SELECT * FROM scored
  WHERE eligible
    AND submitted_date >= DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL 30 DAY)
),
nonzero_stats AS (
  SELECT DISTINCT store, item,
    PERCENTILE_CONT(usage_units, 0.5) OVER (PARTITION BY store, item) AS med_nonzero
  FROM elig_recent
  WHERE usage_units > 0
),
scored_clean AS (
  SELECT e.*, s.med_nonzero,
    (IFNULL(e.override_mode, '') != 'force_include' AND e.usage_units = 0) AS is_zero_usage,
    (IFNULL(e.override_mode, '') != 'force_include'
      AND e.usage_units > 0 AND s.med_nonzero IS NOT NULL
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
    (IFNULL(override_mode, '') != 'force_include'
     AND mad_surv > 0
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
      WHEN override_mode = 'force_exclude' THEN
        FORMAT('%s %s: operator force_exclude',
          FORMAT_DATE('%m/%d', submitted_date), FORMAT_DATE('%a', submitted_date))
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

-- Day-grain audit for Operator Console layout A (Issue #194).
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_inventory_usage_day_audit` AS
WITH base_daily AS (
  SELECT store, item, submitted_date, submitted_ts, quantity_units, raw_text
  FROM (
    SELECT *,
      ROW_NUMBER() OVER (
        PARTITION BY store, submitted_date, item
        ORDER BY
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
  SELECT
    b.store,
    b.item,
    b.submitted_date,
    b.quantity_units  AS curr_close,
    LAG(b.quantity_units) OVER w AS prev_close,
    LAG(b.submitted_date)  OVER w AS prev_date,
    l.orders AS orders_on_day
  FROM base_daily b
  LEFT JOIN `jarvis-bhaga-prod.bhaga.vw_model_labor_daily` l
    ON l.date = b.submitted_date
  WINDOW w AS (PARTITION BY b.store, b.item ORDER BY b.submitted_date)
),
scored_raw AS (
  SELECT *,
    GREATEST(prev_close - curr_close, 0.0) AS usage_units,
    (curr_close - COALESCE(prev_close, 0) > 1.0) AS is_restock,
    -- Signed stock change: negative = consumption, positive = restock/up
    (curr_close - prev_close) AS delta_signed,
    (prev_close IS NOT NULL
      AND DATE_DIFF(submitted_date, prev_date, DAY) = 1
      AND curr_close >= 1.0
      AND COALESCE(orders_on_day, 0) > 0
      AND NOT (curr_close - COALESCE(prev_close, 0) > 1.0)
    ) AS rule_eligible
  FROM transitions
),
scored AS (
  SELECT
    r.*,
    o.mode AS override_mode,
    (
      (r.rule_eligible OR o.mode = 'force_include')
      AND IFNULL(o.mode, '') != 'force_exclude'
    ) AS eligible
  FROM scored_raw r
  LEFT JOIN `jarvis-bhaga-prod.bhaga.inventory_usage_day_overrides` o
    ON o.store = r.store
   AND o.item = r.item
   AND o.submitted_date = r.submitted_date
),
elig_recent AS (
  SELECT * FROM scored
  WHERE eligible
    AND submitted_date >= DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL 30 DAY)
),
nonzero_stats AS (
  SELECT DISTINCT store, item,
    PERCENTILE_CONT(usage_units, 0.5) OVER (PARTITION BY store, item) AS med_nonzero
  FROM elig_recent
  WHERE usage_units > 0
),
scored_clean AS (
  SELECT e.*, s.med_nonzero,
    (IFNULL(e.override_mode, '') != 'force_include' AND e.usage_units = 0) AS is_zero_usage,
    (IFNULL(e.override_mode, '') != 'force_include'
      AND e.usage_units > 0 AND s.med_nonzero IS NOT NULL
      AND e.usage_units < 0.20 * s.med_nonzero) AS is_low_outlier
  FROM elig_recent e
  LEFT JOIN nonzero_stats s USING (store, item)
),
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
    (IFNULL(override_mode, '') != 'force_include'
     AND mad_surv > 0
     AND SAFE_DIVIDE(usage_units - med_surv, 1.4826 * mad_surv) > 2.5) AS is_high_outlier
  FROM hi_mad
),
ranked_dow AS (
  SELECT *,
    ROW_NUMBER() OVER (
      PARTITION BY store, item, EXTRACT(DAYOFWEEK FROM submitted_date)
      ORDER BY submitted_date DESC
    ) AS dow_rn
  FROM hi_scored
  WHERE NOT is_high_outlier
),
in_avg AS (
  SELECT store, item, submitted_date
  FROM ranked_dow
  WHERE dow_rn = 1
),
high_bars AS (
  SELECT DISTINCT store, item, med_surv, mad_surv,
    IF(mad_surv > 0, med_surv + 2.5 * 1.4826 * mad_surv, NULL) AS high_bar
  FROM hi_mad
),
window_days AS (
  SELECT store, item, submitted_date, curr_close AS qty, prev_close, usage_units,
    delta_signed, is_restock, rule_eligible, eligible, override_mode, orders_on_day, prev_date
  FROM scored
  WHERE submitted_date >= DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL 30 DAY)
    AND prev_close IS NOT NULL
),
labeled AS (
  SELECT
    w.store,
    w.item,
    w.submitted_date,
    ROUND(w.qty, 2) AS qty,
    ROUND(w.delta_signed, 2) AS delta,
    w.rule_eligible,
    (ia.submitted_date IS NOT NULL) AS in_avg,
    w.override_mode,
    CASE
      WHEN w.override_mode = 'force_exclude' THEN 'force_exclude'
      WHEN w.override_mode = 'force_include' THEN 'force_include'
      WHEN ia.submitted_date IS NOT NULL THEN 'included'
      WHEN w.is_restock THEN 'restock'
      WHEN DATE_DIFF(w.submitted_date, w.prev_date, DAY) != 1 THEN 'gap'
      WHEN COALESCE(w.orders_on_day, 0) = 0 THEN 'closed'
      WHEN w.qty < 1.0 THEN 'qty<1'
      WHEN w.eligible AND w.usage_units = 0 THEN 'zero usage'
      WHEN w.eligible AND sc.is_low_outlier THEN 'low outlier'
      WHEN hs.is_high_outlier THEN 'high outlier'
      WHEN w.eligible AND ia.submitted_date IS NULL THEN 'superseded weekday'
      ELSE 'excluded'
    END AS reason,
    hb.high_bar,
    CASE
      WHEN hb.high_bar IS NULL THEN TRUE
      WHEN w.usage_units IS NULL THEN NULL
      ELSE w.usage_units <= hb.high_bar
    END AS similar_tomorrow_passes
  FROM window_days w
  LEFT JOIN in_avg ia
    USING (store, item, submitted_date)
  LEFT JOIN scored_clean sc
    USING (store, item, submitted_date)
  LEFT JOIN hi_scored hs
    USING (store, item, submitted_date)
  LEFT JOIN high_bars hb
    USING (store, item)
)
SELECT
  store,
  item,
  submitted_date,
  qty,
  delta,
  rule_eligible,
  in_avg,
  CASE
    WHEN override_mode = 'force_exclude' THEN 'excluded'
    WHEN in_avg OR override_mode = 'force_include' THEN 'included'
    ELSE 'excluded'
  END AS status,
  reason,
  override_mode,
  ROUND(high_bar, 2) AS high_bar,
  similar_tomorrow_passes
FROM labeled
ORDER BY submitted_date DESC, item;
