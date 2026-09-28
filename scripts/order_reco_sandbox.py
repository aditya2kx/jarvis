#!/usr/bin/env python3
"""Sandbox harness for the atomic order-recommendation refresh (Issue #350).

Builds an isolated BigQuery dataset that mirrors prod — every table is a
zero-copy CLONE of prod (prod is only read), every view/routine is recreated
pointing at the sandbox — then applies this branch's pending migrations on
top. Scenario subcommands exercise sp_refresh_order_reco against it.

    python3 scripts/order_reco_sandbox.py provision
    python3 scripts/order_reco_sandbox.py apply --versions 80,81
    python3 scripts/order_reco_sandbox.py parity --caps 80,110,112,120,160
    python3 scripts/order_reco_sandbox.py latency --runs 5
    python3 scripts/order_reco_sandbox.py concurrency --callers 3
    python3 scripts/order_reco_sandbox.py atomicity
    python3 scripts/order_reco_sandbox.py coalesce
    python3 scripts/order_reco_sandbox.py preview
    python3 scripts/order_reco_sandbox.py teardown

Every subcommand prints one JSON summary line (evidence for PR §4) and exits
non-zero when its invariant does not hold.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import json
import os
import re
import statistics
import sys
import time
import uuid
from datetime import date, timedelta

_PROJECT = "jarvis-bhaga-prod"
_PROD = "bhaga"
_DEFAULT_SANDBOX = "bhaga_sandbox_i350"
_STORE = "palmetto"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _client():
    from google.cloud import bigquery
    return bigquery.Client(project=_PROJECT)


def _guard(ds: str) -> None:
    if ds == _PROD or not ds.startswith("bhaga_sandbox"):
        sys.exit(f"order_reco_sandbox: refusing dataset {ds!r} (must start with bhaga_sandbox)")


def _retarget(sql: str, ds: str) -> str:
    return re.sub(r"(?<![\w-])bhaga\.", f"{ds}.", sql)


def _q(sql: str, params: list | None = None):
    from google.cloud import bigquery
    cfg = bigquery.QueryJobConfig(query_parameters=params or [])
    return list(_client().query(sql, job_config=cfg).result())


def _fq(ds: str, name: str) -> str:
    return f"`{_PROJECT}.{ds}.{name}`"


def _emit(summary: dict, ok: bool) -> None:
    summary["ok"] = ok
    print(json.dumps(summary, default=str))
    if not ok:
        sys.exit(1)


# ── provision / teardown ────────────────────────────────────────────────

def provision(ds: str) -> None:
    from google.cloud import bigquery
    c = _client()
    dataset = bigquery.Dataset(f"{_PROJECT}.{ds}")
    dataset.location = "US"
    c.create_dataset(dataset, exists_ok=True)

    tables, views, skipped = [], [], []
    for t in c.list_tables(f"{_PROJECT}.{_PROD}"):
        (tables if t.table_type == "TABLE" else views if t.table_type == "VIEW" else skipped).append(t)
    for t in tables:
        c.query(f"CREATE OR REPLACE TABLE {_fq(ds, t.table_id)} CLONE {_fq(_PROD, t.table_id)}").result()

    pending = {v.table_id: _retarget(c.get_table(v.reference).view_query, ds) for v in views}
    failed: dict[str, str] = {}
    while pending:
        progressed = False
        for name, query in list(pending.items()):
            try:
                c.query(f"CREATE OR REPLACE VIEW {_fq(ds, name)} AS\n{query}").result()
                del pending[name]
                progressed = True
            except Exception as exc:  # dependency view not created yet
                failed[name] = str(exc)[:200]
        if not progressed:
            break

    for r in c.list_routines(f"{_PROJECT}.{_PROD}"):
        full = c.get_routine(r.reference)
        props = dict(full._properties)
        for k in ("etag", "creationTime", "lastModifiedTime"):
            props.pop(k, None)
        props["routineReference"] = {"projectId": _PROJECT, "datasetId": ds, "routineId": r.routine_id}
        props["definitionBody"] = _retarget(props.get("definitionBody", ""), ds)
        c.delete_routine(f"{_PROJECT}.{ds}.{r.routine_id}", not_found_ok=True)
        c.create_routine(bigquery.Routine.from_api_repr(props))

    os.environ["BHAGA_BQ_DATASET"] = ds
    os.environ["BHAGA_DATASTORE"] = "bigquery"
    import core.datastore as datastore
    applied = datastore.ensure_schema()
    _emit({"scenario": "provision", "dataset": ds, "tables_cloned": len(tables),
           "views": len(views) - len(pending), "views_failed": sorted(pending),
           "skipped": [f"{t.table_id}:{t.table_type}" for t in skipped],
           "migrations_applied": applied}, ok=not pending)


def apply(ds: str, versions: list[int]) -> None:
    """Re-run this branch's migrations in the sandbox (iterating on DDL)."""
    os.environ["BHAGA_BQ_DATASET"] = ds
    import core.datastore as datastore
    c = _client()
    done = []
    for version, name, path in datastore._scan_migration_files():
        if version in versions:
            for stmt in datastore._split_statements(datastore._rewrite_dataset(path.read_text())):
                c.query(stmt).result()
            done.append(f"{version:03d}_{name}")
    _emit({"scenario": "apply", "dataset": ds, "migrations": done}, ok=len(done) == len(versions))


def teardown(ds: str) -> None:
    _client().delete_dataset(f"{_PROJECT}.{ds}", delete_contents=True, not_found_ok=True)
    _emit({"scenario": "teardown", "dataset": ds}, ok=True)


# ── procedure + legacy invocations ─────────────────────────────────────

def call_proc(ds: str, *, trigger: str = "sandbox", run_id: str | None = None,
              dry_run: bool = False, pins_override: dict | None = None,
              capacity: int | None = None) -> tuple[str, list, float]:
    from google.cloud import bigquery
    run_id = run_id or f"sbx-{uuid.uuid4().hex[:12]}"
    params = [
        bigquery.ScalarQueryParameter("store", "STRING", _STORE),
        bigquery.ScalarQueryParameter("trigger", "STRING", trigger),
        bigquery.ScalarQueryParameter("by", "STRING", "order_reco_sandbox"),
        bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
        bigquery.ScalarQueryParameter("dry", "BOOL", dry_run),
        bigquery.ScalarQueryParameter("pins", "JSON", json.dumps(pins_override) if pins_override else None),
        bigquery.ScalarQueryParameter("cap", "INT64", capacity),
    ]
    t0 = time.monotonic()
    rows = _q(f"CALL {_fq(ds, 'sp_refresh_order_reco')}(@store, @trigger, @by, @run_id, @dry, "
              f"@pins, @cap, NULL)", params)
    return run_id, rows, time.monotonic() - t0


def legacy_rows(ds: str, cap: int) -> list[dict]:
    """Run the pre-#350 TVF chain in the sandbox and return its generation."""
    live = _fq(ds, "inventory_order_reco")
    slots = [int(r["slot"]) for r in _q(f"SELECT slot FROM {_fq(ds, 'vw_order_reco_next_dates')} ORDER BY slot")]
    gen = f"legacy-{uuid.uuid4().hex[:8]}"
    cols = ("store, Slot, Item, `Current Qty`, `Avg per day`, `On Hand at Restock`, `Order Tubs`, "
            "`Order Weight lbs`, `After Restock`, `Days Left After Restock`, _ord, refreshed_at, "
            "delivery_date, run_id")
    sel = ("Item, `Current Qty`, `Avg per day`, `On Hand at Restock`, `Order Tubs`, `Order Weight lbs`, "
           "`After Restock`, `Days Left After Restock`, _ord, CURRENT_TIMESTAMP(), delivery_date")
    _q(f"INSERT INTO {live} ({cols}) SELECT '{_STORE}', 1, {sel}, '{gen}' "
       f"FROM {_fq(ds, 'tvf_order_reco_slot1')}({cap})")
    for s in slots:
        if s >= 2:
            _q(f"INSERT INTO {live} ({cols}) SELECT '{_STORE}', {s}, {sel}, '{gen}' "
               f"FROM {_fq(ds, 'tvf_order_reco_slot_n')}({cap}, {s})")
    rows = [dict(r) for r in _q(f"SELECT * FROM {live} WHERE run_id = '{gen}'")]
    _q(f"DELETE FROM {live} WHERE run_id = '{gen}'")
    return rows


def reference_inputs(ds: str, cap: int):
    from core.order_reco_reference import ItemInput, RecoInputs
    today = _q("SELECT CURRENT_DATE('America/Chicago') AS d")[0]["d"]
    dates = [r["delivery_date"] for r in _q(
        f"SELECT delivery_date FROM {_fq(ds, 'vw_order_reco_next_dates')} ORDER BY slot")]
    items = [ItemInput(r["item"], float(r["current_qty"]), float(r["avg_daily_usage"] or 0))
             for r in _q(f"SELECT item, current_qty, avg_daily_usage FROM "
                         f"{_fq(ds, 'vw_inventory_order_assistant')} WHERE store = '{_STORE}'")]
    pins = {(r["delivery_date"], r["item"]): int(r["q"]) for r in _q(
        f"SELECT delivery_date, item, SUM(quantity_tubs) q FROM {_fq(ds, 'inventory_order_tub_overrides')} "
        f"WHERE store = '{_STORE}' GROUP BY 1, 2")}
    actuals = {(r["delivery_date"], r["item"]): float(r["q"]) for r in _q(
        f"SELECT delivery_date, item, SUM(quantity_tubs) q FROM {_fq(ds, 'inventory_restock_orders')} "
        f"WHERE store = '{_STORE}' GROUP BY 1, 2")}
    return RecoInputs(as_of=today, capacity=cap, dates=dates, items=items, pins=pins, actuals=actuals)


_CMP = ("current_qty", "avg_daily_usage", "on_hand", "order_tubs", "after_restock", "days_left_after")
_LEGACY_COLS = {"current_qty": "Current Qty", "avg_daily_usage": "Avg per day",
                "on_hand": "On Hand at Restock", "order_tubs": "Order Tubs",
                "after_restock": "After Restock", "days_left_after": "Days Left After Restock"}


def _key(slot, item) -> tuple:
    return int(slot), item


def _diff(a: dict, b: dict, label: str) -> list[str]:
    out = []
    for k in sorted(set(a) | set(b)):
        if k not in a or k not in b:
            out.append(f"{label} {k}: missing on one side")
            continue
        for col in _CMP:
            x, y = a[k].get(col), b[k].get(col)
            if (x is None) != (y is None) or (x is not None and abs(float(x) - float(y)) > 1e-6):
                out.append(f"{label} {k} {col}: {x} != {y}")
    return out


@contextlib.contextmanager
def _synthetic_slots(ds: str):
    """Add two sandbox-only delivery dates after the real ones (one with a pin) so parity
    exercises the water-fill even when every real upcoming date already has Actuals."""
    from google.cloud import bigquery
    last = _q(f"SELECT MAX(delivery_date) d FROM {_fq(ds, 'inventory_restock_schedule')} "
              f"WHERE store = '{_STORE}'")[0]["d"]
    d3, d4 = last + timedelta(days=7), last + timedelta(days=14)
    p = [bigquery.ScalarQueryParameter("d3", "DATE", d3), bigquery.ScalarQueryParameter("d4", "DATE", d4)]
    tag = "'order_reco_sandbox', CURRENT_TIMESTAMP()"
    _q(f"INSERT INTO {_fq(ds, 'inventory_restock_schedule')} (store, delivery_date, updated_by, updated_at) "
       f"VALUES ('{_STORE}', @d3, {tag}), ('{_STORE}', @d4, {tag})", p)
    _q(f"INSERT INTO {_fq(ds, 'inventory_order_tub_overrides')} "
       f"(store, delivery_date, item, quantity_tubs, updated_by, updated_at) "
       f"VALUES ('{_STORE}', @d3, 'Açaí', 10, {tag})", p)
    try:
        yield [d3, d4]
    finally:
        for t in ("inventory_restock_schedule", "inventory_order_tub_overrides"):
            _q(f"DELETE FROM {_fq(ds, t)} WHERE store = '{_STORE}' AND updated_by = 'order_reco_sandbox'")


def parity(ds: str, caps: list[int]) -> None:
    with _synthetic_slots(ds) as synthetic:
        _parity(ds, caps, synthetic)


def _parity(ds: str, caps: list[int], synthetic: list[date]) -> None:
    from core.order_reco_reference import compute_reco
    results, ok = [], True
    for cap in caps:
        _, proc, _ = call_proc(ds, dry_run=True, capacity=cap)
        proc_m = {_key(r["slot"], r["item"]): dict(r) for r in proc}
        ref = compute_reco(reference_inputs(ds, cap))
        ref_m = {_key(r.slot, r.item): {c: getattr(r, c) for c in _CMP} for r in ref.rows}
        leg_m = {_key(r["Slot"], r["Item"]): {c: r[_LEGACY_COLS[c]] for c in _CMP}
                 for r in legacy_rows(ds, cap)}
        proc_vs_ref = _diff(proc_m, ref_m, "proc/ref")
        legacy_vs_ref = _diff(leg_m, ref_m, "legacy/ref")
        tie_slots = sorted(s for s, t in ref.cutoff_tie.items() if t)
        # Legacy has no tie-break; a diff is only acceptable on a slot whose cutoff is a tie
        # (and every later slot, which chains from it).
        unexplained = [d for d in legacy_vs_ref
                       if not tie_slots or int(d.split("(")[1].split(",")[0]) < tie_slots[0]]
        cap_ok = not proc_vs_ref and not unexplained
        ok &= cap_ok
        totals = {k[0]: v["order_tubs"] for k, v in proc_m.items() if k[1] == "TOTAL"}
        sources = {k[0]: v.get("source") for k, v in proc_m.items() if k[1] == "TOTAL"}
        results.append({"cap": cap, "ok": cap_ok, "totals_by_slot": totals, "total_source": sources,
                        "proc_vs_ref": proc_vs_ref[:5], "legacy_vs_ref": legacy_vs_ref[:5],
                        "tie_slots": tie_slots})
    _emit({"scenario": "parity", "synthetic_dates": synthetic, "caps": results}, ok=ok)


def _p95(xs: list[float]) -> float:
    return sorted(xs)[max(0, int(round(0.95 * len(xs))) - 1)]


def latency(ds: str, runs: int) -> None:
    """to_commit = request -> new numbers visible (committed row); call = full CALL incl. coalesce check."""
    from google.cloud import bigquery
    calls, commits = [], []
    for _ in range(runs):
        run_id, _, s = call_proc(ds, trigger="sandbox-latency")
        calls.append(round(s, 2))
        r = _q(f"SELECT TIMESTAMP_DIFF(MAX(IF(status = 'committed', event_at, NULL)), "
               f"MIN(IF(status = 'running', event_at, NULL)), MILLISECOND) / 1000 AS s "
               f"FROM {_fq(ds, 'inventory_order_reco_runs')} WHERE run_id = @r",
               [bigquery.ScalarQueryParameter("r", "STRING", run_id)])
        commits.append(round(float(r[0]["s"]), 2))
    _emit({"scenario": "latency", "to_commit_s": commits, "call_s": calls,
           "p50_to_commit": statistics.median(commits), "p95_to_commit": _p95(commits),
           "p95_call": _p95(calls), "target_p95_to_commit_s": 30}, ok=_p95(commits) <= 30)


def _live_state(ds: str) -> dict:
    r = _q(f"SELECT COUNT(DISTINCT refreshed_at) gens, COUNT(DISTINCT run_id) runs, COUNT(*) n, "
           f"COUNTIF(Source IS NULL) no_source, "
           f"(SELECT COUNT(*) FROM (SELECT Slot, Item FROM {_fq(ds, 'inventory_order_reco')} "
           f" WHERE store = '{_STORE}' GROUP BY 1, 2 HAVING COUNT(*) > 1)) dupes "
           f"FROM {_fq(ds, 'inventory_order_reco')} WHERE store = '{_STORE}'")[0]
    return dict(r)


def _run_statuses(ds: str, run_ids: list[str]) -> dict[str, str]:
    from google.cloud import bigquery
    rows = _q(f"SELECT run_id, status FROM {_fq(ds, 'inventory_order_reco_runs')} WHERE run_id IN UNNEST(@ids) "
              f"QUALIFY ROW_NUMBER() OVER (PARTITION BY run_id ORDER BY event_at DESC) = 1",
              [bigquery.ArrayQueryParameter("ids", "STRING", run_ids)])
    return {r["run_id"]: r["status"] for r in rows}


def concurrency(ds: str, callers: int) -> None:
    ids = [f"sbx-conc-{i}-{uuid.uuid4().hex[:6]}" for i in range(callers)]
    errors = {}
    with concurrent.futures.ThreadPoolExecutor(callers) as pool:
        futs = {pool.submit(call_proc, ds, trigger="sandbox-concurrency", run_id=i): i for i in ids}
        for f in concurrent.futures.as_completed(futs):
            try:
                f.result()
            except Exception as exc:
                errors[futs[f]] = str(exc)[:200]
    state = _live_state(ds)
    statuses = _run_statuses(ds, ids)
    ok = (state["gens"] == 1 and state["dupes"] == 0 and state["no_source"] == 0
          and "committed" in statuses.values()
          and all(s in ("committed", "superseded") for s in statuses.values()))
    _emit({"scenario": "concurrency", "callers": callers, "live": state, "statuses": statuses,
           "errors": errors}, ok=ok)


def atomicity(ds: str) -> None:
    before = _q(f"SELECT MAX(refreshed_at) g, COUNT(*) n FROM {_fq(ds, 'inventory_order_reco')} "
                f"WHERE store = '{_STORE}'")[0]
    run_id = f"sbx-atom-{uuid.uuid4().hex[:6]}"
    raised = None
    try:
        call_proc(ds, trigger="force-error-test", run_id=run_id)
    except Exception as exc:
        raised = str(exc)[:200]
    after = _q(f"SELECT MAX(refreshed_at) g, COUNT(*) n FROM {_fq(ds, 'inventory_order_reco')} "
               f"WHERE store = '{_STORE}'")[0]
    hist = _q(f"SELECT COUNT(*) n FROM {_fq(ds, 'inventory_order_reco_history')} WHERE run_id = '{run_id}'")[0]["n"]
    status = _run_statuses(ds, [run_id]).get(run_id)
    ok = (raised is not None and before["g"] == after["g"] and before["n"] == after["n"]
          and hist == 0 and status == "failed")
    _emit({"scenario": "atomicity", "raised": raised, "live_before": dict(before),
           "live_after": dict(after), "history_rows": hist, "status": status}, ok=ok)


def coalesce(ds: str) -> None:
    """An edit that lands while a run is computing is picked up by the same run."""
    with _synthetic_slots(ds) as (_, d4):
        _coalesce(ds, d4)


def _coalesce(ds: str, d: date) -> None:
    from google.cloud import bigquery
    pins = _fq(ds, "inventory_order_tub_overrides")
    p = [bigquery.ScalarQueryParameter("d", "DATE", d)]
    run_id = f"sbx-coal-{uuid.uuid4().hex[:6]}"
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        fut = pool.submit(call_proc, ds, trigger="sandbox-coalesce", run_id=run_id)
        time.sleep(4)
        _q(f"INSERT INTO {pins} (store, delivery_date, item, quantity_tubs, updated_by, updated_at) "
           f"VALUES ('{_STORE}', @d, 'Ube', 9, 'order_reco_sandbox', CURRENT_TIMESTAMP())", p)
        fut.result()
    live = _q(f"SELECT `Order Tubs` q, Source FROM {_fq(ds, 'inventory_order_reco')} "
              f"WHERE store = '{_STORE}' AND delivery_date = @d AND Item = 'Ube'", p)
    passes = _q(f"SELECT passes FROM {_fq(ds, 'inventory_order_reco_runs')} WHERE run_id = '{run_id}' "
                f"AND status = 'committed' ORDER BY event_at DESC LIMIT 1")
    ok = bool(live) and live[0]["q"] == 9 and live[0]["Source"] == "Manual" and bool(passes) and passes[0]["passes"] >= 2
    result = {"scenario": "coalesce", "delivery_date": d, "live_ube": [dict(r) for r in live],
              "passes": passes[0]["passes"] if passes else None}
    # Leave the sandbox live table computed from its real inputs.
    _q(f"DELETE FROM {pins} WHERE store = '{_STORE}' AND updated_by = 'order_reco_sandbox'")
    _q(f"DELETE FROM {_fq(ds, 'inventory_restock_schedule')} WHERE store = '{_STORE}' AND updated_by = 'order_reco_sandbox'")
    call_proc(ds, trigger="sandbox-coalesce-cleanup")
    _emit(result, ok=ok)


def preview(ds: str) -> None:
    """Dry run with a pin override writes nothing and matches the reference model."""
    from core.order_reco_reference import compute_reco
    before = _live_state(ds)
    runs_before = _q(f"SELECT COUNT(*) n FROM {_fq(ds, 'inventory_order_reco_runs')}")[0]["n"]
    d = _q(f"SELECT delivery_date FROM {_fq(ds, 'vw_order_reco_next_dates')} WHERE slot = 2")[0]["delivery_date"]
    override = {"delivery_date": d.isoformat(), "pins": [{"item": "Açaí", "quantity_tubs": 14}]}
    _, rows, secs = call_proc(ds, dry_run=True, pins_override=override)
    cap = int(rows[0]["capacity"])
    inputs = reference_inputs(ds, cap)
    inputs.pins = {k: v for k, v in inputs.pins.items() if k[0] != d}
    inputs.pins[(d, "Açaí")] = 14
    ref = {_key(r.slot, r.item): {c: getattr(r, c) for c in _CMP} for r in compute_reco(inputs).rows}
    diffs = _diff({_key(r["slot"], r["item"]): dict(r) for r in rows}, ref, "preview/ref")
    after = _live_state(ds)
    runs_after = _q(f"SELECT COUNT(*) n FROM {_fq(ds, 'inventory_order_reco_runs')}")[0]["n"]
    ok = not diffs and before == after and runs_before == runs_after
    _emit({"scenario": "preview", "seconds": round(secs, 2), "diffs": diffs[:5],
           "wrote_nothing": before == after and runs_before == runs_after}, ok=ok)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["provision", "apply", "teardown", "run", "parity", "latency",
                                        "concurrency", "atomicity", "coalesce", "preview"])
    ap.add_argument("--dataset", default=_DEFAULT_SANDBOX)
    ap.add_argument("--caps", default="80,110,112,120,160")
    ap.add_argument("--versions", default="80,81", help="apply: migration versions to re-run")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--callers", type=int, default=3)
    a = ap.parse_args(argv)
    _guard(a.dataset)
    if a.command == "provision":
        provision(a.dataset)
    elif a.command == "apply":
        apply(a.dataset, [int(v) for v in a.versions.split(",")])
    elif a.command == "teardown":
        teardown(a.dataset)
    elif a.command == "run":
        run_id, _, secs = call_proc(a.dataset)
        _emit({"scenario": "run", "run_id": run_id, "seconds": round(secs, 2),
               "live": _live_state(a.dataset)}, ok=True)
    elif a.command == "parity":
        parity(a.dataset, [int(c) for c in a.caps.split(",")])
    elif a.command == "latency":
        latency(a.dataset, a.runs)
    elif a.command == "concurrency":
        concurrency(a.dataset, a.callers)
    elif a.command == "atomicity":
        atomicity(a.dataset)
    elif a.command == "coalesce":
        coalesce(a.dataset)
    elif a.command == "preview":
        preview(a.dataset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
