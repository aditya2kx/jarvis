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

# Real Chrome (channel="chrome" in runtime.py for anti-bot stealth), pinned: the unpinned
# patchright installer pulled Chrome 155 on 2026-10-06 and ADP's bot defense then ended
# every session (Issue #372). Bump only after a forced-scrape run passes on the new version.
ARG CHROME_VERSION=154.0.8037.97
RUN deb="google-chrome-stable_${CHROME_VERSION}-1_amd64.deb" \
    && python3 -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" \
        "https://dl.google.com/linux/chrome/deb/pool/main/g/google-chrome-stable/${deb}" "/tmp/${deb}" \
    && apt-get update \
    && apt-get install -y --no-install-recommends "/tmp/${deb}" \
    && rm -f "/tmp/${deb}" /etc/apt/sources.list.d/google-chrome.list \
    && rm -rf /var/lib/apt/lists/* \
    && google-chrome --version

COPY agents/ agents/
COPY skills/ skills/
COPY core/ core/

ENV TZ=UTC
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python3", "-m", "agents.bhaga.scripts.daily_refresh"]
CMD ["--store", "palmetto"]
