# detector/Dockerfile
#
# Single-stage production image for the gate-detector Flask service.
#
# Build context is the detector/ directory (set in docker-compose.yml).
# Runtime configuration is supplied via .env (env_file in compose) — no
# credentials or zone geometry are baked into the image.

FROM python:3.11-slim

# System runtime deps:
#   ffmpeg   — fallback RTSP capture backend (capture.py subprocess path)
#   libgl1   — linked by opencv-python wheel (headless display stub)
#   libglib2.0-0 — glib runtime needed by OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies before copying the full source so Docker can
# cache this layer independently of code changes.
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
        flask>=3.0 \
        "numpy>=1.24" \
        "opencv-python>=4.8" \
        "python-dotenv>=1.0"

# Copy application source (tests and calibration tools are excluded via
# .dockerignore — they are not needed at runtime).
COPY . .

# Create runtime data directories; they will normally be bind-mounted but
# having them present avoids permission errors if the volume is absent.
RUN mkdir -p .data/debug .data/captures

# Flask service port — runtime value comes from PORT env var (config.py).
EXPOSE 8080

# Start the detector.  The app reads all settings from environment / .env,
# so no CLI flags are needed for normal container operation.
CMD ["python", "app.py"]
