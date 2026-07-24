"""Unit tests for category rule evaluation (Issue #160)."""
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
            _r(id="amazon", priority=270, match_pattern="Amazon", category_id="inv"),
            _r(id="adp", priority=100, match_pattern="Adp Wage", category_id="pay"),
        ]
        m = evaluate_rules(
            {"name": "ORIG CO NAME:ADP WAGE PAY ... Amazon", "amount": 100},
            rules,
        )
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m.rule_id, "adp")
        self.assertEqual(m.category_id, "pay")

    def test_adp_beats_ak_juicy_blob(self):
        rules = [
            _r(
                id="inventory_ak",
                priority=200,
                match_pattern="AK Juicy Bowls",
                category_id="inv",
            ),
            _r(
                id="payroll_adp_wages",
                priority=100,
                match_pattern="Adp Wage Pay",
                category_id="payroll_labor",
                subcategory_id="payroll_labor__adp_wage_pay",
                amount_sign="positive",
            ),
        ]
        name = (
            "ORIG CO NAME:ADP WAGE PAY ORIG ID:9333006057 "
            "IND NAME:AK JUICY BOWLS LLC PAL"
        )
        m = evaluate_rules({"name": name, "merchant_name": None, "amount": 5000}, rules)
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m.rule_id, "payroll_adp_wages")

    def test_amazon_refund_negative(self):
        rules = [
            _r(
                id="refund_amazon",
                priority=50,
                match_pattern="Amazon",
                amount_sign="negative",
                category_id="contra",
            ),
            _r(
                id="inventory_amazon",
                priority=270,
                match_pattern="Amazon",
                amount_sign="positive",
                category_id="inv",
            ),
        ]
        m = evaluate_rules(
            {"name": "AMAZON MKTPL", "merchant_name": "Amazon", "amount": -20},
            rules,
        )
        self.assertEqual(m.rule_id if m else None, "refund_amazon")

    def test_amount_sign_positive(self):
        rules = [
            _r(id="pos", priority=10, match_pattern="Square", amount_sign="positive"),
            _r(id="neg", priority=20, match_pattern="Square", amount_sign="negative"),
        ]
        m = evaluate_rules({"name": "Square Inc", "amount": -100}, rules)
        self.assertEqual(m.rule_id if m else None, "neg")

    def test_contains_any(self):
        rules = [
            _r(
                id="web",
                priority=10,
                match_operator="contains_any",
                match_pattern="Webstaurant|Restaurant Store",
            )
        ]
        m = evaluate_rules(
            {"name": "THE WEBSTAURANT STORE INC", "amount": 50}, rules
        )
        self.assertEqual(m.rule_id if m else None, "web")

    def test_disabled_skipped(self):
        rules = [_r(id="off", priority=1, match_pattern="Amazon", enabled=False)]
        self.assertIsNone(
            evaluate_rules({"name": "Amazon", "amount": 10}, rules)
        )

    def test_invalid_regex_no_throw(self):
        rules = [
            _r(id="bad", priority=1, match_operator="regex", match_pattern="[invalid"),
            _r(id="ok", priority=2, match_pattern="Amazon"),
        ]
        m = evaluate_rules({"name": "Amazon", "amount": 1}, rules)
        self.assertEqual(m.rule_id if m else None, "ok")

    def test_override_beats_rule(self):
        match = evaluate_rules(
            {"name": "Amazon", "amount": 10},
            [_r(id="a", priority=1, match_pattern="Amazon", category_id="inv")],
        )
        eff = effective_category(
            {"override_category_id": "opex", "override_subcategory_id": "supplies"},
            match,
        )
        self.assertEqual(eff["source"], "override")
        self.assertEqual(eff["category_id"], "opex")
        self.assertIsNone(eff["rule_id"])


if __name__ == "__main__":
    unittest.main()
