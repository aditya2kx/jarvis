# skills/tip_pool_allocation

Pure-function pool-by-day fair share computation. **No IO, no network, no clock reads.** Inputs in, outputs out, fully unit-testable.

**Status:** scaffold only. To be implemented in BHAGA milestone M3.

## Why pure?

People get paid based on this skill's output. Correctness is non-negotiable. A pure function:

- Is deterministic (same inputs → same outputs, always)
- Can be exhaustively unit-tested with no mocks
- Has no failure modes related to network/disk/auth
- Can be reasoned about by reading the code, not tracing IO

Any side effects (reading Square, reading ADP, writing the sheet) belong in **other skills** that wrap this one.

## The allocation rule (pool-by-day fairness)

For each individual date:

```
employee_share_for_date = (employee_hours_on_date / total_team_hours_on_date) * tip_pool_for_date
```

Then sum across the period for the per-employee period total.

**Critical**: NEVER pool the whole period's tips against the whole period's hours. That under-rewards employees who worked the high-tip days. The handoff doc and `bhaga.md` rule #5 enshrine this.

## Public API (planned)

```python
from skills.tip_pool_allocation import allocate

result = allocate(
    daily_tips={"2026-04-01": 12450, "2026-04-02": 8900, ...},  # cents per date
    daily_hours={
        ("emp_001", "2026-04-01"): 7.5,
        ("emp_002", "2026-04-01"): 6.0,
        ...
    },
)
# result = {
#   "per_day": [
#     {"date": "2026-04-01", "employee": "emp_001", "hours": 7.5, "share_cents": 6917},
#     {"date": "2026-04-01", "employee": "emp_002", "hours": 6.0, "share_cents": 5533},
#     ...
#   ],
#   "per_period": [
#     {"employee": "emp_001", "total_hours": 38.0, "total_tip_cents": 28140},
#     ...
#   ],
#   "flags": [
#     {"date": "2026-04-05", "issue": "tips_with_no_hours", "tip_cents": 4500},
#   ],
# }
```

## Edge cases

| Case | Handling |
|------|----------|
| Day has tips but zero hours logged | Append to `flags` list (`issue: tips_with_no_hours`); no allocation; do not raise |
| Day has hours but zero tips | Write rows with `share_cents = 0`; no error |
| Rounding residuals (cents don't divide evenly) | Distribute deterministically via **largest-remainder method** so total of shares equals day's tip pool exactly. Never silently absorb residual cents. |
| Negative inputs | Raise `ValueError` — caller bug, fail fast |
| Empty inputs | Return `{"per_day": [], "per_period": [], "flags": []}`; no error |

## Money

All money is **integer cents** — never `float`. The function rejects non-integer `tip_cents` inputs.

## Multi-store

Skill is store-agnostic. The caller (BHAGA) supplies pre-filtered single-shop data. The skill doesn't know or care what shop it's allocating for.

## Test strategy (when implemented)

- Property-based tests: total of per-employee period shares == sum of daily tip pools (modulo rounding)
- Largest-remainder cases: pool of 100¢ across 3 equal employees → [34, 33, 33] (deterministic)
- Edge case coverage: every entry in the table above gets a named test
