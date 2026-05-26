FROM python:3.12-slim-bookworm

LABEL project="jarvis-bhaga" \
      description="BHAGA daily refresh orchestrator with Patchright + Chromium"

# System deps for headless Chromium + virtual framebuffer
RUN apt-get update && apt-get install -y --no-install-recommends \
        libnss3 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libcups2 \
        libdrm2 \
        libxkbcommon0 \
        libxcomposite1 \
        libxdamage1 \
        libxrandr2 \
        libgbm1 \
        libpango-1.0-0 \
        libcairo2 \
        libasound2 \
        libxshmfence1 \
        libx11-xcb1 \
        fonts-liberation \
        xvfb \
        xauth \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download real Chrome (channel="chrome" in runtime.py for anti-bot stealth)
RUN patchright install chrome

COPY agents/ agents/
COPY skills/ skills/
COPY core/ core/

ENV TZ=UTC
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["xvfb-run", "--auto-servernum", "--server-args=-screen 0 1920x1080x24", "python3", "-m", "agents.bhaga.scripts.daily_refresh"]
CMD ["--store", "palmetto"]
