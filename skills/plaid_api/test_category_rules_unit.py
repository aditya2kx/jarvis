"""Unit tests for category rule evaluation (Issue #160).

Patterns are synthetic — never use real merchant / store / personal brands.
"""
from __future__ import annotations

import unittest

from skills.plaid_api.category_rules import (
    CategoryRule,
    effective_category,
    evaluate_rules,
)


def _r(**kwargs) -> CategoryRule:
    defaults = dict(
        id="r",
        priority=100,
        match_field="name_or_merchant",
        match_operator="contains",
        match_pattern="x",
        amount_sign="any",
        category_id="cat",
        subcategory_id="sub",
        enabled=True,
    )
    defaults.update(kwargs)
    return CategoryRule(**defaults)


class TestEvaluateRules(unittest.TestCase):
    def test_priority_order(self):
        rules = [
            _r(id="market", priority=270, match_pattern="MarketCo", category_id="inv"),
            _r(id="payroll", priority=100, match_pattern="PayCo Wage", category_id="pay"),
        ]
        m = evaluate_rules(
            {"name": "ORIG CO NAME:PAYCO WAGE PAY ... MarketCo", "amount": 100},
            rules,
        )
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m.rule_id, "payroll")
        self.assertEqual(m.category_id, "pay")

    def test_payroll_beats_entity_blob_in_ind_name(self):
        rules = [
            _r(
                id="inventory_entity",
                priority=200,
                match_pattern="Acme Entity Llc",
                category_id="inv",
            ),
            _r(
                id="payroll_wages",
                priority=100,
                match_pattern="Payco Wage Pay",
                category_id="payroll_labor",
                subcategory_id="payroll_labor__wage_pay",
                amount_sign="positive",
            ),
        ]
        name = (
            "ORIG CO NAME:PAYCO WAGE PAY ORIG ID:9333006057 "
            "IND NAME:ACME ENTITY LLC PAL"
        )
        m = evaluate_rules({"name": name, "merchant_name": None, "amount": 5000}, rules)
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m.rule_id, "payroll_wages")

    def test_refund_negative(self):
        rules = [
            _r(
                id="refund_market",
                priority=50,
                match_pattern="MarketCo",
                amount_sign="negative",
                category_id="contra",
            ),
            _r(
                id="inventory_market",
                priority=270,
                match_pattern="MarketCo",
                amount_sign="positive",
                category_id="inv",
            ),
        ]
        m = evaluate_rules(
            {"name": "MARKETCO MKTPL", "merchant_name": "MarketCo", "amount": -20},
            rules,
        )
        self.assertEqual(m.rule_id if m else None, "refund_market")

    def test_amount_sign_positive(self):
        rules = [
            _r(id="pos", priority=10, match_pattern="PosCo", amount_sign="positive"),
            _r(id="neg", priority=20, match_pattern="PosCo", amount_sign="negative"),
        ]
        m = evaluate_rules({"name": "PosCo Inc", "amount": -100}, rules)
        self.assertEqual(m.rule_id if m else None, "neg")

    def test_contains_any(self):
        rules = [
            _r(
                id="supply",
                priority=10,
                match_operator="contains_any",
                match_pattern="SupplyCo|Restaurant Supply",
            )
        ]
        m = evaluate_rules(
            {"name": "THE SUPPLYCO STORE INC", "amount": 50}, rules
        )
        self.assertEqual(m.rule_id if m else None, "supply")

    def test_disabled_skipped(self):
        rules = [_r(id="off", priority=1, match_pattern="MarketCo", enabled=False)]
        self.assertIsNone(
            evaluate_rules({"name": "MarketCo", "amount": 10}, rules)
        )

    def test_override_beats_rule(self):
        match = evaluate_rules(
            {"name": "MarketCo", "amount": 10},
            [_r(id="a", priority=1, match_pattern="MarketCo", category_id="inv")],
        )
        eff = effective_category(
            {
                "name": "MarketCo",
                "amount": 10,
                "override_category_id": "opex",
                "override_subcategory_id": "supplies",
            },
            match,
        )
        self.assertEqual(eff["source"], "override")
        self.assertEqual(eff["category_id"], "opex")


if __name__ == "__main__":
    unittest.main()
