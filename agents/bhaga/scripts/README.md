# BHAGA Scripts

Agent-specific orchestration scripts. The reusable extraction/allocation/writing logic lives in `skills/` (`square_tips`, `adp_run_automation`, `tip_pool_allocation`, `tip_ledger_writer`); this directory holds the BHAGA-specific glue that wires those skills together for the Austin (and future) workflow.

## Planned scripts

| Script | Purpose | Milestone |
|--------|---------|-----------|
| `pull_tips.py` | Invoke `skills.square_tips` for a date range, write raw output to knowledge-base | M1 |
| `pull_hours.py` | Invoke `skills.adp_run_automation` for a date range, write raw output to knowledge-base | M2 |
| `allocate.py` | Combine tips + hours, run `skills.tip_pool_allocation`, write per-day + per-period output | M3 |
| `write_sheet.py` | Push allocation output into the Austin tip ledger sheet via `skills.tip_ledger_writer` | M1 (minimal) → M4 (paste block) |
| `run_period.py` | End-to-end orchestrator for a pay period: pull → allocate → write → notify Slack | M4 |

Nothing implemented yet — scaffold only. The first working script (M1) will be `pull_tips.py` + the minimal `write_sheet.py` slice that drops a "Tips Today" column into the existing sheet.
