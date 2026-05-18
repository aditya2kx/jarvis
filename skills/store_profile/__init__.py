"""store_profile - Read store identity, aliases, exclusions, and config from
the BHAGA Model Google Sheet (the canonical source of truth).

For the data model and migration history, see ./reader.py.
"""

from skills.store_profile.reader import (
    load_aliases,
    load_config_kv,
    load_employee_roster,
    load_exclusions,
    load_full_profile,
    write_alias,
)

__all__ = [
    "load_aliases",
    "load_config_kv",
    "load_employee_roster",
    "load_exclusions",
    "load_full_profile",
    "write_alias",
]
