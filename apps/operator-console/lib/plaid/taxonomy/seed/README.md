# Accounting category taxonomy seed (private)

**Do not commit merchant, brand, store, or personal match patterns.**

Live seed CSVs (taxonomy + ordered rules) live outside git:

1. Preferred: set `PLAID_TAXONOMY_SEED_DIR` to an ops-managed directory
2. Or: worktree-local `local/plaid-taxonomy-seed/` (gitignored via repo `local/`)

Required files in that directory:

| File | Role |
|---|---|
| `category_taxonomy.csv` | Category / subcategory labels + P&L treatment |
| `transaction_rule_seed.csv` | Ordered match rules (`match_pattern`, amount sign, …) |
| `extension_rules.csv` | Optional corpus extensions (same columns as rules) |

Load into BQ (idempotent MERGE):

```bash
export PLAID_TAXONOMY_SEED_DIR="$HOME/path/to/private-seed"   # or rely on local/
BHAGA_DATASTORE=bigquery python3 -c "
from skills.plaid_api.taxonomy_seed import seed_taxonomy, extend_corpus_rules
print(seed_taxonomy(dry_run=False))
print(extend_corpus_rules(dry_run=False))
"
```

Runtime categorization reads **only from BigQuery** (`plaid_taxonomy_nodes`,
`plaid_category_rules`). The Operator Console Rules drawer edits BQ directly.
Unit tests use synthetic fixture patterns — never copy production seed CSVs here.
