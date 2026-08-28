FROM python:3.12-slim-bookworm

WORKDIR /app

# ffmpeg pulls the HLS frames; there is no lighter way to decode h264 segments.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY cloud/pup_watch/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Detector weights are fetched at build time, not committed: a 136MB binary has
# no business in git. Pinned by digest so the image is reproducible and a
# swapped upstream file fails the build instead of silently changing accuracy.
ARG DETECTOR_URL=https://huggingface.co/Xenova/yolov9-c_all/resolve/main/onnx/model.onnx
ARG DETECTOR_SHA256=008ce02156c1c6e7e636302afd4e14bb04d0c2e291274904414d040505847d6b
RUN mkdir -p /app/models \
    && curl -fsSL "$DETECTOR_URL" -o /app/models/detector.onnx \
    && echo "${DETECTOR_SHA256}  /app/models/detector.onnx" | sha256sum -c -

COPY cloud/__init__.py ./cloud/__init__.py
COPY cloud/pup_watch ./cloud/pup_watch

ENV PORT=8080
ENV PYTHONPATH=/app
ENV PUPWATCH_MODEL_PATH=/app/models/detector.onnx
EXPOSE 8080

# One worker, two threads: a poll is a single short request and concurrency
# would only multiply the ONNX memory footprint.
CMD exec gunicorn --bind :$PORT --workers 1 --threads 2 --timeout 300 cloud.pup_watch.app:app
