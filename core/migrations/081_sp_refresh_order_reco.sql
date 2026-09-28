-- jarvis:script
-- 081_sp_refresh_order_reco.sql
-- Issue #350: one atomic, store-parametric order-recommendation refresh.
--
-- Replaces the slot-by-slot TVF chain (tvf_order_reco_slot1 / _slot_n) that
-- re-evaluated vw_inventory_order_assistant once per slot and committed each
-- slot separately. This procedure reads inputs once into temp tables, computes
-- every slot, and swaps the generation in a single transaction together with
-- its append-only history rows. When an input changes mid-run (fingerprint
-- differs after the pass) it recomputes, up to 3 passes, so the last edit wins.
--
-- Water-fill (unchanged semantics, deterministic ties):
--   budget  = floor(capacity - sum(on-hand at arrival)) - sum(pinned tubs)
--   the k-th extra tub for an item scores (on_hand + k - 1) / avg_per_day;
--   the lowest `budget` scores win, ties broken by item name then k.
--   A date with any Actuals uses the Actuals for every item.
--
-- p_dry_run returns the computed rows without writing anything.
-- p_pins_override (dry run) = {"delivery_date": "YYYY-MM-DD", "pins": [{"item", "quantity_tubs"}]}
--   replaces that date's pins for the preview.
-- p_capacity_override / p_as_of replace the configured capacity / today (NULL = live).

CREATE OR REPLACE PROCEDURE `jarvis-bhaga-prod.bhaga.sp_refresh_order_reco`(
  p_store STRING,
  p_trigger STRING,
  p_requested_by STRING,
  p_run_id STRING,
  p_dry_run BOOL,
  p_pins_override JSON,
  p_capacity_override INT64,
  p_as_of DATE
)
BEGIN
  DECLARE v_as_of DATE DEFAULT COALESCE(p_as_of, CURRENT_DATE('America/Chicago'));
  DECLARE v_cap INT64;
  DECLARE v_dates ARRAY<STRUCT<slot INT64, delivery_date DATE, prev_date DATE>>;
  DECLARE v_fp INT64;
  DECLARE v_fp_after INT64;
  DECLARE v_gen TIMESTAMP;
  DECLARE v_pass INT64 DEFAULT 0;
  DECLARE v_in_txn BOOL DEFAULT FALSE;
  DECLARE v_override_date DATE DEFAULT SAFE_CAST(JSON_VALUE(p_pins_override, '$.delivery_date') AS DATE);

  IF NOT COALESCE(p_dry_run, FALSE) THEN
    INSERT INTO `jarvis-bhaga-prod.bhaga.inventory_order_reco_runs`
      (run_id, store, status, event_at, trigger, requested_by, as_of_date)
    VALUES (p_run_id, p_store, 'running', CURRENT_TIMESTAMP(), p_trigger, p_requested_by, v_as_of);
  END IF;

  BEGIN
    LOOP
      SET v_pass = v_pass + 1;
      -- Fingerprint, capacity and the upcoming delivery dates in one statement.
      -- Today counts as a delivery date until tonight's base closing lands.
      SET (v_fp, v_cap, v_dates) = (
        WITH
          cfg AS (
            SELECT key, value FROM `jarvis-bhaga-prod.bhaga.store_config`
            WHERE store = p_store AND key IN ('order_reco_max_tubs', 'order_reco_max_slots')
            QUALIFY ROW_NUMBER() OVER (PARTITION BY key ORDER BY updated_at DESC) = 1
          ),
          upcoming AS (
            SELECT delivery_date, ROW_NUMBER() OVER (ORDER BY delivery_date) AS slot
            FROM (
              SELECT DISTINCT delivery_date
              FROM `jarvis-bhaga-prod.bhaga.inventory_restock_schedule`
              WHERE store = p_store
                AND (
                  delivery_date > v_as_of
                  OR (
                    delivery_date = v_as_of
                    AND NOT EXISTS (
                      SELECT 1 FROM `jarvis-bhaga-prod.bhaga.inventory_closing_daily` c
                      WHERE c.store = p_store AND c.category = 'base' AND c.submitted_date = v_as_of
                    )
                  )
                )
            )
          )
        SELECT AS STRUCT
          IF(COALESCE(p_dry_run, FALSE), NULL, (
            SELECT inputs_fingerprint FROM `jarvis-bhaga-prod.bhaga.vw_order_reco_inputs_fingerprint`
            WHERE store = p_store)),
          COALESCE(p_capacity_override,
                   (SELECT SAFE_CAST(value AS INT64) FROM cfg WHERE key = 'order_reco_max_tubs'), 120),
          ARRAY(
            SELECT AS STRUCT slot, delivery_date, LAG(delivery_date) OVER (ORDER BY slot) AS prev_date
            FROM upcoming
            WHERE slot <= COALESCE((SELECT SAFE_CAST(value AS INT64) FROM cfg WHERE key = 'order_reco_max_slots'), 4)
            ORDER BY slot
          )
      );

      -- The assistant view is evaluated exactly once per pass.
      CREATE OR REPLACE TEMP TABLE _oa AS
      SELECT
        item, current_qty,
        COALESCE(avg_daily_usage, 0) AS avg_daily_usage,
        reported, days_considered, excluded_days
      FROM `jarvis-bhaga-prod.bhaga.vw_inventory_order_assistant`
      WHERE store = p_store;

      -- Actuals and pins for the planned dates, read once.
      CREATE OR REPLACE TEMP TABLE _locks AS
      SELECT 'actual' AS kind, delivery_date, item, SUM(quantity_tubs) AS tubs
      FROM `jarvis-bhaga-prod.bhaga.inventory_restock_orders`
      WHERE store = p_store AND delivery_date IN (SELECT delivery_date FROM UNNEST(v_dates))
      GROUP BY delivery_date, item
      UNION ALL
      SELECT 'pin', delivery_date, item, SUM(quantity_tubs)
      FROM (
        SELECT delivery_date, item, quantity_tubs
        FROM `jarvis-bhaga-prod.bhaga.inventory_order_tub_overrides`
        WHERE store = p_store
          AND delivery_date IN (SELECT delivery_date FROM UNNEST(v_dates))
          AND (v_override_date IS NULL OR delivery_date != v_override_date)
        UNION ALL
        SELECT v_override_date, JSON_VALUE(p, '$.item'), SAFE_CAST(JSON_VALUE(p, '$.quantity_tubs') AS INT64)
        FROM UNNEST(COALESCE(JSON_QUERY_ARRAY(p_pins_override, '$.pins'), [])) AS p
        WHERE v_override_date IS NOT NULL
      )
      GROUP BY delivery_date, item;

      CREATE OR REPLACE TEMP TABLE _reco (
        slot INT64, delivery_date DATE, item STRING,
        current_qty FLOAT64, avg_daily_usage FLOAT64, on_hand FLOAT64,
        order_tubs INT64, weight FLOAT64, after_restock FLOAT64,
        days_left_after FLOAT64, _ord INT64, source STRING
      );

      FOR d IN (SELECT slot, delivery_date, prev_date FROM UNNEST(v_dates) ORDER BY slot) DO
        INSERT INTO _reco
        WITH
          -- Slot 1 burns from today's qty; later slots chain from the previous
          -- slot's stored (rounded) on-hand + integer order.
          arr AS (
            SELECT o.item, o.current_qty, o.avg_daily_usage,
              GREATEST(
                IF(d.slot = 1,
                   o.current_qty - DATE_DIFF(d.delivery_date, v_as_of, DAY) * o.avg_daily_usage,
                   (r.on_hand + r.order_tubs) - DATE_DIFF(d.delivery_date, d.prev_date, DAY) * o.avg_daily_usage),
                0) AS on_hand_arrival
            FROM _oa o
            LEFT JOIN _reco r ON r.item = o.item AND r.slot = d.slot - 1
            WHERE d.slot = 1 OR r.item IS NOT NULL
          ),
          act AS (SELECT item, tubs AS actual_tubs FROM _locks WHERE kind = 'actual' AND delivery_date = d.delivery_date),
          has_act AS (SELECT COUNT(*) > 0 AS is_actual FROM act),
          ov AS (SELECT item, tubs AS override_tubs FROM _locks WHERE kind = 'pin' AND delivery_date = d.delivery_date),
          budget AS (
            SELECT GREATEST(
              CAST(FLOOR(v_cap - SUM(on_hand_arrival)) AS INT64)
                - CAST((SELECT COALESCE(SUM(override_tubs), 0) FROM ov) AS INT64),
              0
            ) AS tubs_budget
            FROM arr
          ),
          candidates AS (
            SELECT a.item, k, (a.on_hand_arrival + k - 1) / a.avg_daily_usage AS sort_key
            FROM arr a
            CROSS JOIN budget b
            CROSS JOIN UNNEST(GENERATE_ARRAY(1, LEAST(b.tubs_budget, 300))) AS k
            LEFT JOIN ov ON ov.item = a.item
            WHERE a.item != 'Blade' AND a.avg_daily_usage > 0 AND ov.item IS NULL
          ),
          ranked AS (
            SELECT item, ROW_NUMBER() OVER (ORDER BY sort_key, item, k) AS rn FROM candidates
          ),
          est AS (
            SELECT item, COUNT(*) AS order_tubs
            FROM ranked CROSS JOIN budget b
            WHERE rn <= b.tubs_budget
            GROUP BY item
          ),
          fin AS (
            SELECT
              a.item, a.current_qty, a.avg_daily_usage, a.on_hand_arrival,
              CASE
                WHEN h.is_actual THEN COALESCE(ac.actual_tubs, 0)
                WHEN ov.item IS NOT NULL THEN ov.override_tubs
                ELSE COALESCE(e.order_tubs, 0)
              END AS order_tubs,
              CASE
                WHEN h.is_actual THEN 'Actuals'
                WHEN ov.item IS NOT NULL THEN 'Manual'
                ELSE 'Estimated'
              END AS source
            FROM arr a
            CROSS JOIN has_act h
            LEFT JOIN act ac USING (item)
            LEFT JOIN ov USING (item)
            LEFT JOIN est e USING (item)
          ),
          rows_ AS (
            SELECT
              item, current_qty, avg_daily_usage,
              ROUND(on_hand_arrival, 2) AS on_hand,
              order_tubs,
              ROUND(on_hand_arrival + order_tubs, 2) AS after_restock,
              ROUND(SAFE_DIVIDE(on_hand_arrival + order_tubs, NULLIF(avg_daily_usage, 0)), 1) AS days_left_after,
              CASE WHEN item = 'Blade' THEN NULL
                   ELSE order_tubs * (CASE WHEN item = 'Açaí' THEN 18 ELSE 20 END) END AS weight,
              source
            FROM fin
          )
        SELECT d.slot, d.delivery_date, item, current_qty, avg_daily_usage, on_hand,
          CAST(ROUND(order_tubs) AS INT64), weight, after_restock, days_left_after, 0, source
        FROM rows_
        UNION ALL
        SELECT d.slot, d.delivery_date, 'TOTAL',
          ROUND(SUM(current_qty), 2),
          ROUND(SUM(avg_daily_usage), 2),
          ROUND(SUM(on_hand), 2),
          CAST(ROUND(SUM(order_tubs)) AS INT64),
          ROUND(SUM(weight) + 50 * CEIL(SAFE_DIVIDE(SUM(order_tubs), 40)), 0),
          ROUND(SUM(after_restock), 2),
          ROUND(SAFE_DIVIDE(SUM(after_restock), NULLIF(SUM(avg_daily_usage), 0)), 1),
          1,
          IF((SELECT is_actual FROM has_act), 'Actuals', 'Estimated')
        FROM rows_;
      END FOR;

      IF COALESCE(p_dry_run, FALSE) THEN
        SELECT slot, delivery_date, item, current_qty, avg_daily_usage, on_hand, order_tubs,
          weight, after_restock, days_left_after, _ord, source, v_cap AS capacity, v_as_of AS as_of_date
        FROM _reco
        ORDER BY slot, _ord, current_qty DESC;
        RETURN;
      END IF;

      SET v_gen = CURRENT_TIMESTAMP();
      BEGIN TRANSACTION;
      SET v_in_txn = TRUE;

      -- One statement swaps the live generation: new rows in, every older row out.
      MERGE `jarvis-bhaga-prod.bhaga.inventory_order_reco` t
      USING (
        SELECT p_store AS store, slot, item, current_qty, avg_daily_usage, on_hand, order_tubs,
          weight, after_restock, days_left_after, _ord, delivery_date, source
        FROM _reco
      ) n
      ON FALSE
      WHEN NOT MATCHED BY TARGET THEN
        INSERT (store, Slot, Item, `Current Qty`, `Avg per day`, `On Hand at Restock`, `Order Tubs`,
                `Order Weight lbs`, `After Restock`, `Days Left After Restock`, _ord, refreshed_at,
                delivery_date, Source, run_id)
        VALUES (n.store, n.slot, n.item, n.current_qty, n.avg_daily_usage, n.on_hand, n.order_tubs,
                n.weight, n.after_restock, n.days_left_after, n._ord, v_gen, n.delivery_date,
                n.source, p_run_id)
      WHEN NOT MATCHED BY SOURCE AND t.store = p_store THEN
        DELETE;

      IF p_trigger = 'force-error-test' THEN
        RAISE USING MESSAGE = 'order_reco forced error after live insert (atomicity test)';
      END IF;

      INSERT INTO `jarvis-bhaga-prod.bhaga.inventory_order_reco_history`
        (store, run_id, refreshed_at, delivery_date, Slot, Item, `Current Qty`, `Avg per day`,
         `On Hand at Restock`, `Order Tubs`, `Order Weight lbs`, `After Restock`,
         `Days Left After Restock`, _ord, Source)
      SELECT p_store, p_run_id, v_gen, delivery_date, slot, item, current_qty, avg_daily_usage,
        on_hand, order_tubs, weight, after_restock, days_left_after, _ord, source
      FROM _reco;

      INSERT INTO `jarvis-bhaga-prod.bhaga.inventory_order_reco_runs`
        (run_id, store, status, event_at, trigger, requested_by, refreshed_at,
         inputs_fingerprint, capacity, as_of_date, passes, inputs)
      SELECT p_run_id, p_store, 'committed', CURRENT_TIMESTAMP(), p_trigger, p_requested_by,
        v_gen, v_fp, v_cap, v_as_of, v_pass,
        TO_JSON(STRUCT(
          v_cap AS capacity,
          v_dates AS dates,
          ARRAY(SELECT AS STRUCT delivery_date, item, tubs FROM _locks WHERE kind = 'pin' ORDER BY delivery_date, item) AS pins,
          ARRAY(SELECT AS STRUCT delivery_date, item, tubs FROM _locks WHERE kind = 'actual' ORDER BY delivery_date, item) AS actuals,
          ARRAY(SELECT AS STRUCT * FROM _oa ORDER BY item) AS items
        ));

      COMMIT TRANSACTION;
      SET v_in_txn = FALSE;

      SET v_fp_after = (
        SELECT inputs_fingerprint FROM `jarvis-bhaga-prod.bhaga.vw_order_reco_inputs_fingerprint`
        WHERE store = p_store
      );
      IF v_fp_after IS NOT DISTINCT FROM v_fp OR v_pass >= 3 THEN
        LEAVE;
      END IF;
    END LOOP;
  EXCEPTION WHEN ERROR THEN
    IF v_in_txn THEN
      ROLLBACK TRANSACTION;
    END IF;
    INSERT INTO `jarvis-bhaga-prod.bhaga.inventory_order_reco_runs`
      (run_id, store, status, event_at, trigger, requested_by, as_of_date, passes, error)
    VALUES (
      p_run_id, p_store,
      IF(REGEXP_CONTAINS(@@error.message, r'(?i)concurrent update|transaction is aborted|could not serialize'), 'superseded', 'failed'),
      CURRENT_TIMESTAMP(), p_trigger, p_requested_by, v_as_of, v_pass, @@error.message
    );
    IF NOT REGEXP_CONTAINS(@@error.message, r'(?i)concurrent update|transaction is aborted|could not serialize') THEN
      RAISE USING MESSAGE = CONCAT('order_reco_failed run_id=', p_run_id, ': ', @@error.message);
    END IF;
  END;
END;
