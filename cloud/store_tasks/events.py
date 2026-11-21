"""Task events. The API publishes; consumers (Slack bridge, warehouse) subscribe.

The API never calls Slack. With no topic configured, events are kept in memory (tests, local).
"""

from __future__ import annotations

import base64
import json
import logging
import os

import google.auth
from google.auth.transport.requests import AuthorizedSession

log = logging.getLogger("store_tasks")

TASK_COMPLETED = "task.completed"
TASK_REOPENED = "task.reopened"
TASK_CREATED = "task.created"
SHIFT_INCOMPLETE = "shift.incomplete"
COUNT_SUBMITTED = "count.submitted"


class Events:
    def __init__(self, topic: str | None = None, project: str | None = None) -> None:
        self.topic = topic if topic is not None else os.environ.get("STORE_TASKS_EVENTS_TOPIC", "")
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        self.sent: list[dict] = []
        self._session: AuthorizedSession | None = None

    def publish(self, kind: str, store: str, payload: dict) -> None:
        event = {"type": kind, "store": store, **payload}
        if not self.topic:
            self.sent.append(event)
            return
        try:
            if self._session is None:
                creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/pubsub"])
                self._session = AuthorizedSession(creds)
            r = self._session.post(
                f"https://pubsub.googleapis.com/v1/projects/{self.project}/topics/{self.topic}:publish",
                json={"messages": [{
                    "data": base64.b64encode(json.dumps(event).encode()).decode(),
                    "attributes": {"type": kind, "store": store},
                }]},
                timeout=10,
            )
            r.raise_for_status()
        except Exception as e:
            # A lost notification must not fail the tick that caused it.
            log.error("store-tasks fail reason=event_publish type=%s store=%s err=%r", kind, store, e)
