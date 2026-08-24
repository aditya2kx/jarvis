FROM python:3.12-slim-bookworm

WORKDIR /app

COPY cloud/tesla_aladdin_garage/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY skills/__init__.py ./skills/__init__.py
COPY skills/tesla_fleet ./skills/tesla_fleet
COPY skills/aladdin_connect ./skills/aladdin_connect
COPY cloud/__init__.py ./cloud/__init__.py
COPY cloud/tesla_aladdin_garage ./cloud/tesla_aladdin_garage

ENV PORT=8080
ENV PYTHONPATH=/app
ENV GARAGE_WORKER=1
EXPOSE 8080

CMD exec gunicorn --bind :$PORT --workers 1 --threads 2 --timeout 120 cloud.tesla_aladdin_garage.app:app
