"""skills/tip_ledger_writer - Idempotent writes into BHAGA's three Google Sheets workbooks.

Public surface (see writer.py for docstrings):
    * write_raw_adp_shifts        (BHAGA ADP Raw > shifts)
    * write_raw_adp_punches       (BHAGA ADP Raw > punches)
    * write_raw_adp_rates         (BHAGA ADP Raw > wage_rates)
    * write_raw_square_transactions (BHAGA Square Raw > transactions)
    * write_raw_square_daily_rollup (BHAGA Square Raw > daily_rollup)

All writes are upserts keyed by each tab's natural key (defined in schema.py).
Rows that don't match any incoming record are preserved, which makes backfill
and daily incremental refreshes compose cleanly without truncating history.
"""

from skills.tip_ledger_writer.reader import (
    read_raw_adp_punches,
    read_raw_adp_rates,
    read_raw_adp_shifts,
    read_raw_square_daily_rollup,
    read_raw_square_transactions,
)
from skills.tip_ledger_writer.schema import WORKBOOK_SCHEMAS, get_tab_spec
from skills.tip_ledger_writer.writer import (
    write_raw_adp_punches,
    write_raw_adp_rates,
    write_raw_adp_shifts,
    write_raw_square_daily_rollup,
    write_raw_square_transactions,
)

__all__ = [
    "WORKBOOK_SCHEMAS",
    "get_tab_spec",
    # writers
    "write_raw_adp_shifts",
    "write_raw_adp_punches",
    "write_raw_adp_rates",
    "write_raw_square_transactions",
    "write_raw_square_daily_rollup",
    # readers
    "read_raw_adp_shifts",
    "read_raw_adp_punches",
    "read_raw_adp_rates",
    "read_raw_square_transactions",
    "read_raw_square_daily_rollup",
]
