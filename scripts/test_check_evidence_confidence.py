#!/usr/bin/env python3
"""Unit tests for scripts/check_evidence_confidence.py."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from scripts.check_evidence_confidence import main, parse_score


def test_parses_rating_phrasing():
    assert parse_score("### Evidence confidence rating: **85%**") == 85


def test_parses_colon_phrasing():
    assert parse_score("Evidence confidence: 96%") == 96


def test_parses_plain_rating():
    assert parse_score("Evidence confidence rating: 100%") == 100


def test_missing_score_is_none():
    assert parse_score("no score in here") is None


def test_main_fails_below_min():
    assert main(["--text", "Evidence confidence rating: **85%**", "--min", "95"]) == 1


def test_main_passes_at_or_above_min():
    assert main(["--text", "Evidence confidence rating: 96%", "--min", "95"]) == 0


def test_main_missing_is_noop():
    assert main(["--text", "nothing here", "--min", "95"]) == 0
