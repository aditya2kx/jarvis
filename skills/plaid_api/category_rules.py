"""Pure Copilot-style category rule evaluation (Issue #160).

Mirrored by apps/operator-console/lib/plaid/category-rules.ts — keep semantics
identical. Priority ascending, first enabled match wins.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Optional

MatchField = Literal["name", "merchant_name", "name_or_merchant"]
MatchOperator = Literal["contains", "contains_any", "equals_or_contains", "regex"]
AmountSign = Literal["positive", "negative", "any"]


@dataclass(frozen=True)
class CategoryRule:
    id: str
    priority: int
    match_field: str
    match_operator: str
    match_pattern: str
    amount_sign: Optional[str]
    category_id: str
    subcategory_id: Optional[str]
    enabled: bool = True
    account_mask: Optional[str] = None


@dataclass(frozen=True)
class RuleMatch:
    rule_id: str
    category_id: str
    subcategory_id: Optional[str]


def _haystack(txn: dict[str, Any], field: str) -> str:
    name = (txn.get("name") or "") or ""
    merchant = (txn.get("merchant_name") or "") or ""
    if field == "name":
        return name
    if field == "merchant_name":
        return merchant
    # name_or_merchant (and seed "name" loaded as this): search both
    return f"{name} {merchant}".strip()


def _amount_ok(amount: Any, sign: Optional[str]) -> bool:
    if sign is None or sign == "" or sign == "any":
        return True
    try:
        a = float(amount or 0)
    except (TypeError, ValueError):
        return False
    if sign == "positive":
        return a > 0
    if sign == "negative":
        return a < 0
    return True


def _field_matches(text: str, operator: str, pattern: str) -> bool:
    if not pattern:
        return False
    hay = text.casefold()
    op = (operator or "contains").strip().lower()
    if op == "contains":
        return pattern.casefold() in hay
    if op == "contains_any":
        parts = [p.strip() for p in pattern.split("|") if p.strip()]
        return any(p.casefold() in hay for p in parts)
    if op == "equals_or_contains":
        p = pattern.casefold().strip()
        return hay.strip() == p or p in hay
    if op == "regex":
        try:
            return re.search(pattern, text, flags=re.IGNORECASE) is not None
        except re.error:
            print(f"[plaid categorize] skip invalid regex rule pattern={pattern!r}")
            return False
    return False


def _account_mask_ok(txn_mask: Any, rule_mask: Optional[str]) -> bool:
    want = "".join(c for c in (rule_mask or "") if c.isdigit())[-4:]
    if not want:
        return True
    got = "".join(c for c in str(txn_mask or "") if c.isdigit())[-4:]
    return len(got) == 4 and got == want


def rule_matches(txn: dict[str, Any], rule: CategoryRule) -> bool:
    if not rule.enabled:
        return False
    if not _amount_ok(txn.get("amount"), rule.amount_sign):
        return False
    if not _account_mask_ok(txn.get("account_mask"), rule.account_mask):
        return False
    field = rule.match_field or "name_or_merchant"
    # Seed CSV uses match_field=name but merchants often hold the clean string —
    # always search name + merchant for contains-family ops unless merchant_name
    # is explicitly requested.
    if field == "name":
        field = "name_or_merchant"
    text = _haystack(txn, field)
    return _field_matches(text, rule.match_operator, rule.match_pattern)


def evaluate_rules(txn: dict[str, Any], rules: list[CategoryRule]) -> Optional[RuleMatch]:
    """First enabled rule by ascending priority; None if none match."""
    ordered = sorted(
        (r for r in rules if r.enabled),
        key=lambda r: (r.priority, r.id),
    )
    for rule in ordered:
        try:
            if rule_matches(txn, rule):
                return RuleMatch(
                    rule_id=rule.id,
                    category_id=rule.category_id,
                    subcategory_id=rule.subcategory_id,
                )
        except Exception as exc:  # noqa: BLE001
            print(f"[plaid categorize] skip rule_id={rule.id}: {exc}")
            continue
    return None


def effective_category(
    txn: dict[str, Any],
    match: Optional[RuleMatch],
) -> dict[str, Any]:
    """override → rule → none."""
    oc = txn.get("override_category_id")
    if oc:
        return {
            "category_id": oc,
            "subcategory_id": txn.get("override_subcategory_id"),
            "rule_id": None,
            "source": "override",
        }
    if match:
        return {
            "category_id": match.category_id,
            "subcategory_id": match.subcategory_id,
            "rule_id": match.rule_id,
            "source": "rule",
        }
    return {
        "category_id": None,
        "subcategory_id": None,
        "rule_id": None,
        "source": "none",
    }
