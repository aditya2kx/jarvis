FROM python:3.12-slim-bookworm

WORKDIR /app

# Build context is the repo root (see deploy.yml).
COPY cloud/webhook/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cloud/webhook/handler.py .
COPY skills/__init__.py ./skills/__init__.py
COPY skills/plaid_api ./skills/plaid_api
# Team pulse (Issue #216 / #223): handler imports agents.bhaga.scripts.team_pulse.
# Pre-#223 image was handler+plaid only → POST /team-pulse failed with
# `No module named 'agents'`. Namespace packages (no __init__.py) are fine.
COPY agents/bhaga/scripts ./agents/bhaga/scripts
COPY skills/clickup_chat ./skills/clickup_chat
COPY core ./core

ENV PORT=8080
ENV PYTHONPATH=/app
ENV BHAGA_SECRETS_BACKEND=gcp
EXPOSE 8080

CMD exec gunicorn --bind :$PORT --workers 1 --threads 2 --timeout 30 handler:app
