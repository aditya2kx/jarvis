# One image, two services: the Tasks API (default) and the Slack bridge (APP_MODULE override).
FROM python:3.12-slim-bookworm

WORKDIR /app

COPY cloud/store_tasks/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cloud/__init__.py ./cloud/__init__.py
COPY cloud/store_tasks ./cloud/store_tasks
COPY cloud/store_tasks_slack ./cloud/store_tasks_slack
COPY skills/__init__.py ./skills/__init__.py
COPY skills/google_signin ./skills/google_signin
COPY skills/clickup_chat ./skills/clickup_chat

ENV PORT=8080
ENV PYTHONPATH=/app
ENV APP_MODULE=cloud.store_tasks.wsgi:app
EXPOSE 8080

ENV GUNICORN_WORKERS=1
CMD exec gunicorn --bind :$PORT --workers $GUNICORN_WORKERS --threads 8 --timeout 120 $APP_MODULE
