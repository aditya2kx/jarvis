"""gunicorn entry point for the Tasks API."""

from cloud.store_tasks.app import create_app

app = create_app()
