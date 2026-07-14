#!/usr/bin/env python3
"""Parse ADP RUN 'Payroll Liability' report text into employer-burden fields.

Source: Taxes → Tax reports → Payroll Liability (see
docs/operator-console/adp-forward-labor-spike.md).
"""

from __future__ import annotations

import datetime
import re
from typing import Optional

_FROM_RE = re.compile(
    r"From:\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*-\s*(.+?)(?:\s+To:|\s+Edit|\s+Print|$)",
    re.IGNORECASE,
)
_MONEY = r"([\d,]+\.\d{2})"


def _money(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    return float(s.replace(",", ""))


def parse_payroll_liability_text(text: str) -> dict:
    """Extract ER tax lines + effective burden from a Payroll Liability body."""
    if not text:
        raise ValueError("empty Payroll Liability text")

    m = _FROM_RE.search(text)
    if not m:
        raise ValueError("could not parse check_date from Payroll Liability header")
    check_date = datetime.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    label_tail = m.group(4).strip()
    payroll_label = f"{m.group(1)}/{m.group(2)}/{m.group(3)} - {label_tail}"

    ss = re.search(rf"Social Security\s+{_MONEY}\s+{_MONEY}", text, re.I)
    ee_ss = _money(ss.group(1)) if ss else None
    er_ss = _money(ss.group(2)) if ss else None

    med = re.search(rf"Medicare\s+{_MONEY}\s+{_MONEY}", text, re.I)
    er_med = _money(med.group(2)) if med else None

    futa = re.search(rf"Federal Unemployment Tax Act\s+[\d.]+\s+{_MONEY}", text, re.I)
    er_futa = _money(futa.group(1)) if futa else None

    sui = re.search(rf"State Unemployment \(Employer\)\s+[\d.]+\s+{_MONEY}", text, re.I)
    er_sui = _money(sui.group(1)) if sui else None

    # "Total Taxes 1,304.40 1,130.62 2,435.02" → EE, ER, combined
    tt = re.search(rf"Total Taxes\s+{_MONEY}\s+{_MONEY}\s+{_MONEY}", text, re.I)
    ee_tax_total = _money(tt.group(1)) if tt else None
    er_tax_total = _money(tt.group(2)) if tt else None
    if er_tax_total is None:
        parts = [x for x in (er_ss, er_med, er_futa, er_sui) if x is not None]
        er_tax_total = round(sum(parts), 2) if parts else None

    pbp = re.search(rf"Total Pay-by-Pay Insurance\s+\${_MONEY}", text, re.I)
    if not pbp:
        pbp = re.search(rf"Pay-by-Pay Insurance\s+{_MONEY}", text, re.I)
    pay_by_pay = _money(pbp.group(1)) if pbp else None

    wage_base = round(ee_ss / 0.062, 2) if ee_ss and ee_ss > 0 else None
    burden_num = (er_tax_total or 0.0) + (pay_by_pay or 0.0)
    effective = round(burden_num / wage_base, 4) if wage_base and wage_base > 0 else None

    if er_tax_total is None and er_ss is None:
        raise ValueError("no employer tax lines found in Payroll Liability text")

    return {
        "check_date": check_date.isoformat(),
        "payroll_label": payroll_label,
        "er_social_security": er_ss,
        "er_medicare": er_med,
        "er_futa": er_futa,
        "er_sui": er_sui,
        "er_tax_total": er_tax_total,
        "pay_by_pay": pay_by_pay,
        "ee_tax_total": ee_tax_total,
        "approx_ss_wage_base": wage_base,
        "effective_burden_pct": effective,
    }
