FROM python:3.12-slim-bookworm

WORKDIR /app

COPY cloud/jarvis_dev_gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cloud/__init__.py ./cloud/__init__.py
COPY cloud/jarvis_dev_gateway ./cloud/jarvis_dev_gateway

ENV PORT=8080
ENV PYTHONPATH=/app
EXPOSE 8080

CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 60 cloud.jarvis_dev_gateway.app:app
