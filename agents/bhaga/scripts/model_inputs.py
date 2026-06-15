"""BQ readers for human inputs — replaces Sheet config/employees/training tab reads.

These functions return the **same shapes** as the Sheet readers they replace so
all call sites stay semantics-identical after the swap:

  Sheet reader                                  → BQ reader (this module)
  --------------------------------------------- ---------------------------------
  _read_training_shifts_from_sheet(...)         → read_training_shifts(store)
  _read_training_excluded_from_sheet(...)       → read_training_excluded(store)
  store_profile.load_aliases(store)             → read_aliases(store)
  store_profile.load_exclusions(store)          → read_exclusions(store)

All functions degrade gracefully (return empty) when BQ is unavailable
(BHAGA_DATASTORE != "bigquery") so the pipeline still runs in laptop-dev
mode that has no BQ access.
"""
from __future__ import annotations

import datetime
import pathlib


def read_training_shifts(store: str = "palmetto") -> set[tuple[str, str]]:
    """Return {(canonical_name, 'YYYY-MM-DD')} from BQ training_shifts.

    Replaces update_model_sheet._read_training_shifts_from_sheet().
    """
    try:
        from core.datastore import read_query, fq
        rows = read_query(
            f"SELECT employee_name, CAST(date AS STRING) AS d"
            f" FROM {fq('training_shifts')}"
            f" WHERE store = '{store}'"
        )
        return {
            (r["employee_name"].strip(), r["d"])
            for r in rows
            if r.get("employee_name") and r.get("d")
        }
    except Exception as exc:  # noqa: BLE001
        print(f"  [model_inputs] WARN: read_training_shifts failed: {exc}")
        return set()


def read_training_excluded(store: str = "palmetto") -> dict[str, datetime.date]:
    """Return {canonical_name: last_training_date} from store_config.

    Reads store_config keys whose name starts with 'training_excluded:'.
    Replaces update_model_sheet._read_training_excluded_from_sheet() and
    process_reviews._read_training_excluded().
    """
    try:
        from core.store_config import get_all
        out: dict[str, datetime.date] = {}
        for key, val in get_all(store).items():
            if not key.startswith("training_excluded:"):
                continue
            raw = (val or "").strip()
            if not raw:
                continue
            try:
                out[key.split(":", 1)[1].strip()] = datetime.date.fromisoformat(raw)
            except ValueError:
                print(f"  [model_inputs] WARN: unparseable training_excluded date for {key!r}: {raw!r}")
        return out
    except Exception as exc:  # noqa: BLE001
        print(f"  [model_inputs] WARN: read_training_excluded failed: {exc}")
        return {}


def read_aliases(store: str = "palmetto") -> dict[str, str]:
    """Return {raw_or_canonical: canonical} from BQ employee_aliases.

    Canonical names map to themselves (canonical → canonical is always in the dict).
    Replaces store_profile.load_aliases().
    """
    try:
        from core.datastore import read_query, fq
        rows = read_query(
            f"SELECT raw_name, canonical_name"
            f" FROM {fq('employee_aliases')}"
            f" WHERE store = '{store}'"
        )
        out: dict[str, str] = {}
        for r in rows:
            canonical = r["canonical_name"]
            out[canonical] = canonical
            out[r["raw_name"]] = canonical
        return out
    except Exception as exc:  # noqa: BLE001
        print(f"  [model_inputs] WARN: read_aliases failed: {exc}")
        return {}


def read_punches_bq(store: str = "palmetto") -> list[dict]:
    """Return ADP punches from BQ adp_punches table as list[dict].

    Each dict has keys: date (str YYYY-MM-DD), employee_name (str canonical),
    in_time (str HH:MM), out_time (str HH:MM).  Shape matches the dicts
    returned by read_raw_adp_punches() so process_reviews callers are unchanged.
    """
    try:
        from core.datastore import read_query, fq
        rows = read_query(
            f"SELECT CAST(date AS STRING) AS date, canonical_name AS employee_name,"
            f" in_time, out_time, employee_id"
            f" FROM {fq('adp_punches')}"
            f" ORDER BY date, employee_id, in_time"
        )
        return [dict(r) for r in rows if r.get("employee_name") and r.get("date")]
    except Exception as exc:  # noqa: BLE001
        print(f"  [model_inputs] WARN: read_punches_bq failed: {exc}")
        return []


def read_exclusions(store: str = "palmetto") -> dict:
    """Return {'permanent': [...], 'training': {name: 'YYYY-MM-DD'}} from BQ/store_config.

    'permanent' comes from store_config key 'excluded_from_tip_pool' (semicolon-separated
    canonical names), with fallback to the store profile JSON when the key is not set.
    'training' comes from store_config keys matching 'training_excluded:*'.

    Replaces store_profile.load_exclusions().
    """
    try:
        from core.store_config import get_config, get_all
        permanent_raw = (get_config(store, "excluded_from_tip_pool") or "").strip()
        if permanent_raw:
            if ";" in permanent_raw:
                permanent: list[str] = [n.strip() for n in permanent_raw.split(";") if n.strip()]
            else:
                permanent = [permanent_raw]
        else:
            # Fall back to store profile JSON (bootstrap value, rarely changes).
            import json
            profile_path = (
                pathlib.Path(__file__).parents[2]
                / "bhaga"
                / "knowledge-base"
                / "store-profiles"
                / f"{store}.json"
            )
            p = json.loads(profile_path.read_text())
            permanent = list(
                p.get("employees", {}).get("excluded_from_tip_pool_and_labor_pct", [])
            )
        training: dict[str, str] = {
            k.split(":", 1)[1].strip(): v
            for k, v in get_all(store).items()
            if k.startswith("training_excluded:") and (v or "").strip()
        }
        return {"permanent": permanent, "training": training}
    except Exception as exc:  # noqa: BLE001
        print(f"  [model_inputs] WARN: read_exclusions failed: {exc}")
        return {"permanent": [], "training": {}}
