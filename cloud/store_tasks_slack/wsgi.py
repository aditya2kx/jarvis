"""gunicorn entry point for the Slack bridge."""

from cloud.store_tasks_slack.app import create_app

app = create_app()
