"""Unit tests for payroll_liability_backend parser."""
from __future__ import annotations

import pytest

from skills.adp_run_automation import payroll_liability_backend as plb

_SAMPLE = (
    "From: 7/17/2026 - Payroll 1 To: 7/17/2026 - Payroll 1 Edit Print "
    "Total Cash Required $12,682.84 "
    "Social Security 712.33 712.31 Medicare 166.59 166.58 "
    "Federal Unemployment Tax Act 0.6000 40.66 "
    "State Unemployment (Employer) 2.7000 211.07 "
    "Total Taxes 1,304.40 1,130.62 2,435.02 "
    "Total Pay-by-Pay Insurance $43.08"
)


def test_parse_payroll_liability_sample():
    rec = plb.parse_payroll_liability_text(_SAMPLE)
    assert rec["check_date"] == "2026-07-17"
    assert rec["er_social_security"] == 712.31
    assert rec["er_medicare"] == 166.58
    assert rec["er_futa"] == 40.66
    assert rec["er_sui"] == 211.07
    assert rec["er_tax_total"] == 1130.62
    assert rec["pay_by_pay"] == 43.08
    assert rec["effective_burden_pct"] == pytest.approx(0.1022, abs=0.001)


def test_parse_empty_raises():
    with pytest.raises(ValueError):
        plb.parse_payroll_liability_text("")
