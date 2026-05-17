"""skills/tip_pool_allocation — pure-function pool-by-day fair share computation.

This is the one piece of BHAGA where correctness is non-negotiable: people
get paid based on this module's output. No IO, no network, no clock reads.
Inputs in, outputs out. Fully unit-testable in isolation.

Per `agents/bhaga/bhaga.md` rules 4, 5, 11:
    - Rule  4: Allocation is pure — no network/disk/time.
    - Rule  5: Pool-by-day fairness (NEVER pool the whole period's tips
               against the whole period's hours).
    - Rule 11: Rounding residuals distributed deterministically via the
               largest-remainder method so total shares equal tip pool
               exactly. Never silently absorb residual cents.

Public API:
    from skills.tip_pool_allocation import allocate, AllocationResult

    result = allocate(
        daily_tips={"2026-04-01": 12450, ...},       # cents per date
        daily_hours={("emp_001", "2026-04-01"): 7.5, ...},
    )
    # result.per_day: [{date, employee, hours, share_cents}, ...]
    # result.per_period: [{employee, total_hours, total_tip_cents}, ...]
    # result.flags: [{date, issue, ...}, ...]

See `adapter.allocate` for the full contract.
"""

from .adapter import allocate, AllocationResult, Flag

__all__ = ["allocate", "AllocationResult", "Flag"]
