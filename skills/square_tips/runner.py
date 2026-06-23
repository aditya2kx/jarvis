"""Square runner — browser-based scrape removed (2026-06-23).

Square data ingestion now uses the Square OAuth REST API via
``skills/square_api/ingest.py`` and ``skills/square_api/kds_reporting.py``.
This module retains only the error-class stubs so any existing references
continue to import without error while the codebase is cleaned up.
"""

from __future__ import annotations


class SquareDeviceBlockedError(RuntimeError):
    """Retained for reference; no longer raised — Square uses OAuth API, not browser."""


class _RetryFreshLogin(Exception):
    """Retained for reference; no longer raised — Square uses OAuth API, not browser."""


class ScrapeLockHeldError(RuntimeError):
    """Retained for reference; no longer raised — Square uses OAuth API, not browser."""
