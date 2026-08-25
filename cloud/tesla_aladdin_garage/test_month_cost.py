"""Month Cursor ledger helpers for garage notify."""

from cloud.tesla_aladdin_garage.month_cost import (
    format_cursor_cost_lines,
    format_cursor_cost_subject,
    month_cursor_cost,
)


def test_format_within_cap():
    info = {"ok": True, "usd": 4.12, "budget_usd": 10.0, "month": "2026-08"}
    lines = format_cursor_cost_lines(info)
    assert "$4.12 / $10.00" in lines[0]
    assert "within cap" in lines[0]
    assert format_cursor_cost_subject(info) == "Cursor $4.12/$10"


def test_format_over_cap():
    info = {"ok": True, "usd": 12.5, "budget_usd": 10.0, "month": "2026-08"}
    lines = format_cursor_cost_lines(info)
    assert "OVER CAP" in lines[0]
    assert "Remaining vs cap" in lines[1]


def test_format_unavailable():
    info = {"ok": False, "usd": None, "budget_usd": 10.0, "month": "2026-08", "error": "RuntimeError"}
    lines = format_cursor_cost_lines(info)
    assert "unavailable" in lines[0]
    assert format_cursor_cost_subject(info) == "Cursor n/a"


def test_month_cursor_cost_fail_open():
    def boom():
        raise RuntimeError("nope")

    out = month_cursor_cost(query_fn=boom)
    assert out["ok"] is False
    assert out["error"] == "RuntimeError"
    assert out["budget_usd"] == 10.0


def test_month_cursor_cost_ok():
    out = month_cursor_cost(query_fn=lambda: 3.5)
    assert out["ok"] is True
    assert out["usd"] == 3.5
