# skills/tip_ledger_writer

Thin wrapper on `skills/google_sheets/` that writes BHAGA's three tip-ledger views into a target Google Sheet:

1. **Daily ledger tab** — one row per (date, employee): hours, day's tip pool, employee share
2. **Per-period summary tab** — one row per employee: total hours, total tips, pay period dates
3. **Paste-block tab** — rows formatted for ADP Time Sheet Import (Co Code, Batch ID, File #, Reg Hours, Earnings 3 Code = tips earnings code, Earnings 3 Amount, etc.) — ready for the user to copy and paste into RUN

**Status:** scaffold only. M1 will implement the minimal "Tips Today" slice (one column write into the existing daily tab). Full three-tab design lands at M4.

## Built on top of

- `skills/google_sheets/` — already wired with `user-palmetto-google` (and `user-google-drive-sheets` as fallback). No new auth needed.

## Public API (planned)

```python
from skills.tip_ledger_writer import write_daily, write_period_summary, write_paste_block

write_daily(
    sheet_id="1abc...",
    tab_name="Daily Ledger",
    rows=[{"date": "2026-04-01", "employee": "Maria Garcia", "hours": 7.5, "share_cents": 6917}, ...],
)

write_period_summary(
    sheet_id="1abc...",
    tab_name="Period Summary",
    period_start="2026-04-01",
    period_end="2026-04-14",
    rows=[{"employee": "Maria Garcia", "total_hours": 38.0, "total_tip_cents": 28140}, ...],
)

write_paste_block(
    sheet_id="1abc...",
    tab_name="ADP Paste",
    co_code="ABC",
    batch_id="2026-04-14-TIPS",
    earnings_code="3",  # the shop's ADP tips earnings code
    rows=[{"file_number": "12345", "reg_hours": 38.0, "tip_amount_cents": 28140}, ...],
)
```

## Idempotency

Re-running for the same date / period MUST overwrite that date's / period's rows, not append duplicates. Implementation strategy: write keys identify (date, employee) for the daily tab and (employee, period) for the summary tab; existing matching rows are replaced in place.

## Money formatting

- Internal: integer cents
- Sheet output: dollars formatted as `$1,234.56` for human readability
- Paste block: format as required by ADP Time Sheet Import (typically dollars-and-cents with 2 decimals, no `$` symbol)

## Open questions resolved at first session (M1)

These come from BHAGA's open-questions list — `tip_ledger_writer` cannot start writing until they're answered:

- **Sheet ID + owning Google account** (Palmetto vs personal — picks which MCP to use)
- **Daily tab header row** — exact column names + a sample row, so the write goes into the right column
- **Cash tips column** — does the existing sheet track declared cash tips? If yes, this skill leaves that column untouched
- **ADP earnings code** — needed for M4 paste-block tab
- **Pay period schedule** — biweekly / weekly / semi-monthly — determines period roll-up boundaries

## Multi-store

`sheet_id` is a parameter. Houston (September 2026) gets its own sheet (or its own tab in a combined sheet — defer until Houston onboarding). The skill writes wherever it's pointed.
