"""Unit tests for skills/plaid_api/categorize (no live BQ)."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from skills.plaid_api import categorize
from skills.plaid_api.category_rules import CategoryRule

SQUARE = CategoryRule(
    id="income_square_deposit", priority=10, match_field="name_or_merchant",
    match_operator="contains", match_pattern="SQUARECO", amount_sign="negative",
    category_id="revenue_deposits", subcategory_id="revenue_deposits__square_deposits",
)
ADP = CategoryRule(
    id="payroll_adp_wages", priority=100, match_field="name_or_merchant",
    match_operator="contains", match_pattern="PAYCO WAGE", amount_sign="positive",
    category_id="payroll_labor", subcategory_id="payroll_labor__adp_wage_pay",
)
HEB = CategoryRule(
    id="inventory_heb", priority=260, match_field="name_or_merchant",
    match_operator="contains", match_pattern="GROCERCO", amount_sign="positive",
    category_id="inventory_food_supplies", subcategory_id="inventory_food_supplies__heb_grocery",
)


def _txn(tid: str, name: str, amount: float, **kw) -> dict:
    return {
        "transaction_id": tid, "name": name, "merchant_name": None, "amount": amount,
        "account_mask": "1111", "counterparty_name": None, "category_id": None,
        "subcategory_id": None, "rule_id": None, "override_category_id": None,
        "is_internal": False, **kw,
    }


class TestPlanUpdates(unittest.TestCase):
    def test_uncategorized_aug_sep_rows_get_existing_rules(self):
        # Shape of rows ingested 2026-08-11..09-14 that stayed categorized_at NULL (synthetic names).
        rows = [
            _txn("sq", "ORIG CO NAME:SQUARECO SETTLE", -500.0),
            _txn("adp", "ORIG CO NAME:PAYCO WAGE PAY", 1234.56),
            _txn("heb", "GROCERCO #123", 42.0, account_mask="2222"),
            _txn("cava", "SOME CAFE", 12.0),
        ]
        plan = categorize.plan_updates(rows, [SQUARE, ADP, HEB])
        got = {u["transaction_id"]: u["rule_id"] for u in plan["updates"]}
        self.assertEqual(
            got, {"sq": "income_square_deposit", "adp": "payroll_adp_wages", "heb": "inventory_heb"}
        )
        self.assertEqual(plan["unchanged"], 1)

    def test_override_never_touched(self):
        rows = [_txn("o", "PAYCO WAGE PAY", 10.0, override_category_id="personal")]
        plan = categorize.plan_updates(rows, [ADP])
        self.assertEqual(plan["updates"], [])
        self.assertEqual(plan["skipped_override"], 1)

    def test_already_correct_is_unchanged(self):
        rows = [_txn("a", "PAYCO WAGE PAY", 10.0, category_id="payroll_labor",
                     subcategory_id="payroll_labor__adp_wage_pay", rule_id="payroll_adp_wages")]
        self.assertEqual(categorize.plan_updates(rows, [ADP])["updates"], [])


def _bq_with_rows(rows: list[dict]) -> MagicMock:
    bq = MagicMock()

    def _query(sql, job_config=None):
        job = MagicMock()
        job.result.return_value = rows if "FROM `jarvis-bhaga-prod.bhaga.plaid_transactions` t" in sql else []
        return job

    bq.query.side_effect = _query
    return bq


class TestReapply(unittest.TestCase):
    @patch.object(categorize, "load_rules", return_value=[ADP])
    def test_post_sync_sweeps_never_categorized_rows(self, _rules):
        bq = _bq_with_rows([_txn("adp", "PAYCO WAGE PAY", 5.0)])
        out = categorize.categorize_upserted(bq, [], item_id="item1")
        select_sql = bq.query.call_args_list[0].args[0]
        self.assertIn("t.categorized_at IS NULL AND t.override_category_id IS NULL", select_sql)
        self.assertIn("t.item_id = @item_id", select_sql)
        self.assertEqual(out["updated"], 1)

    @patch.object(categorize, "load_rules", return_value=[ADP])
    def test_scope_is_ids_or_uncategorized(self, _rules):
        bq = _bq_with_rows([])
        categorize.categorize_upserted(bq, ["t1"], item_id="item1")
        sql = bq.query.call_args_list[0].args[0]
        self.assertIn(
            "(t.transaction_id IN UNNEST(@ids) OR (t.categorized_at IS NULL AND "
            "t.override_category_id IS NULL))",
            sql,
        )

    @patch.object(categorize, "load_rules", return_value=[ADP])
    def test_merge_uses_struct_param_not_shared_staging(self, _rules):
        bq = _bq_with_rows([_txn("adp", "PAYCO WAGE PAY", 5.0)])
        categorize.reapply_categories(bq)
        merge_sql = bq.query.call_args_list[1].args[0]
        self.assertIn("UNNEST(@rows)", merge_sql)
        self.assertIn("T.override_category_id IS NULL", merge_sql)
        bq.load_table_from_json.assert_not_called()

    @patch.object(categorize, "load_rules", return_value=[ADP])
    def test_no_merge_when_nothing_changes(self, _rules):
        bq = _bq_with_rows([_txn("c", "SOME CAFE", 5.0)])
        categorize.reapply_categories(bq)
        self.assertEqual(bq.query.call_count, 1)


class TestSerializeRetry(unittest.TestCase):
    def test_retries_concurrent_update_then_succeeds(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError(
                    "Could not serialize access to table jarvis-bhaga-prod:bhaga.plaid_transactions "
                    "due to concurrent update"
                )
            return "ok"

        slept: list[float] = []
        self.assertEqual(categorize._with_serialize_retry(flaky, label="t", sleep=slept.append), "ok")
        self.assertEqual(slept, [2, 5])

    def test_other_errors_raise_immediately(self):
        def boom():
            raise ValueError("syntax error")

        slept: list[float] = []
        with self.assertRaises(ValueError):
            categorize._with_serialize_retry(boom, label="t", sleep=slept.append)
        self.assertEqual(slept, [])

    def test_gives_up_after_last_attempt(self):
        def always():
            raise RuntimeError("Could not serialize access due to concurrent update")

        with self.assertRaises(RuntimeError):
            categorize._with_serialize_retry(always, label="t", sleep=lambda _s: None)


if __name__ == "__main__":
    unittest.main()
