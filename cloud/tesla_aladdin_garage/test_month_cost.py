"""Tesla Fleet $10 discount helpers for garage notify."""

from cloud.tesla_aladdin_garage import persist
from cloud.tesla_aladdin_garage.month_cost import (
    estimate_usd,
    format_tesla_cost_lines,
    format_tesla_cost_subject,
    month_tesla_cost,
)


def test_estimate_dashboard_august_data():
    assert round(estimate_usd({"data": 602}), 2) == 1.20


def test_format_within_cap():
    info = {
        "ok": True,
        "usd": 1.20,
        "budget_usd": 10.0,
        "month": "2026-08",
        "data": 602,
        "signals": 0,
        "commands": 0,
        "wakes": 0,
    }
    lines = format_tesla_cost_lines(info)
    assert "$1.20 / $10.00" in lines[0]
    assert "within cap" in lines[0]
    assert "Data 602" in lines[1]
    assert format_tesla_cost_subject(info) == "Tesla Fleet $1.20/$10"


def test_format_over_cap():
    info = {"ok": True, "usd": 12.5, "budget_usd": 10.0, "month": "2026-08"}
    lines = format_tesla_cost_lines(info)
    assert "OVER CAP" in lines[0]


def test_format_unavailable():
    info = {"ok": False, "usd": None, "budget_usd": 10.0, "month": "2026-08", "error": "RuntimeError"}
    lines = format_tesla_cost_lines(info)
    assert "unavailable" in lines[0]
    assert format_tesla_cost_subject(info) == "Tesla Fleet n/a"


def test_month_tesla_cost_from_snapshot():
    out = month_tesla_cost(snapshot_fn=lambda _m: {"data": 500, "commands": 0, "wakes": 0, "signals": 0})
    assert out["ok"] is True
    assert out["usd"] == 1.0
    assert out["data"] == 500


def test_record_billable_memory(monkeypatch):
    monkeypatch.delenv("GARAGE_PERSIST", raising=False)
    persist._usage_mem.clear()
    persist.set_client(None)
    persist.record_billable("data", 3)
    persist.record_billable("signals", 10)
    snap = persist.load_tesla_usage(list(persist._usage_mem.keys())[0])
    assert snap["data"] == 3
    assert snap["signals"] == 10
    persist._usage_mem.clear()
