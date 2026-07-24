# Palmetto June Transaction Rule Seed

This rule seed is based on the June 2026 Austin Palmetto transaction analysis discussed in chat. It is intended as a starting taxonomy for a co-pilot budgeting/accounting app that fetches transactions and suggests categories/subcategories from transaction-name/account patterns.

## Files

- `palmetto_transaction_rule_seed.csv`: main rule table for pattern matching.
- `palmetto_category_taxonomy.csv`: category/subcategory taxonomy and accounting treatment.
- `palmetto_rule_implementation_notes.csv`: design notes for rule precedence, confidence, overrides, and P&L caveats.
- `palmetto_transaction_rules_workbook.xlsx`: all three tables in one workbook.

## Suggested app behavior

1. Match by `priority` ascending.
2. Use `match_field`, `match_operator`, `match_pattern`, and `amount_sign`.
3. Create a suggested category/subcategory/accounting line.
4. Surface medium/low confidence suggestions for review.
5. Allow user override in UI and save the override as a more specific future rule.
6. Keep cash-flow categorization separate from P&L treatment, especially for payroll taxes/withholding and unpaid invoices.
