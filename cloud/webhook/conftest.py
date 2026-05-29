"""Pytest path shim for the webhook tests.

``handler.py`` is deployed as a standalone module (its container sets the
working directory to ``cloud/webhook`` and runs ``handler`` directly), so
``test_handler.py`` imports it with a bare ``import handler``. That resolves
fine when pytest is invoked from inside this directory, but a repo-root
``pytest`` run uses package-import mode and cannot find a top-level
``handler`` module, producing a collection error.

Inserting this directory onto ``sys.path`` at collection time lets the bare
import resolve from any working directory without changing the production
import contract or the test's import line.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
