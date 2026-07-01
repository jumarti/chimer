# detector/Dockerfile
#
# Single-stage production image for the gate-detector Flask service.
#
# Build context is the detector/ directory (set in docker-compose.yml).
# Runtime configuration is supplied via .env (env_file in compose) — no
# credentials or zone geometry are baked into the image.
#
# The image defaults to a non-root "detector" user (UID 1000).  When run via
# docker compose, the calling host user overrides this via
#   user: "${UID}:${GID}"
# so files written to bind-mounted volumes appear on the host owned by you.

FROM python:3.11-slim

# System runtime deps:
#   ffmpeg   — fallback RTSP capture backend (capture.py subprocess path)
#   libgl1   — linked by opencv-python wheel (headless display stub)
#   libglib2.0-0 — glib runtime needed by OpenCV
#   tzdata   — timezone database (needed to honour TZ env var)
RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

# Timezone — Python's time/datetime functions honour this env var directly.
ENV TZ=America/Bogota

# Create a dedicated non-root user.  UID 1000 matches most first-user Linux
# accounts, which reduces chown work when running without compose overrides.
RUN useradd --no-log-init -r -u 1000 -s /sbin/nologin detector

WORKDIR /app

# Install Python dependencies before copying the full source so Docker can
# cache this layer independently of code changes.  pyproject.toml is NOT
# copied here — the deps are listed explicitly in the RUN command so that
# a patch-version bump in pyproject.toml does not invalidate this layer.
RUN pip install --no-cache-dir \
        "flask>=3.0" \
        "numpy>=1.24" \
        "opencv-python>=4.8" \
        "python-dotenv>=1.0"

# Copy application source (tests and calibration tools are excluded via
# .dockerignore — they are not needed at runtime).
COPY . .

# Create data mount-point directories, then hand the whole tree to the
# detector user so the process can write even without a bind mount.
RUN mkdir -p .data/debug .data/captures \
    && chown -R detector /app

# Default to the non-root user.  docker compose overrides this at runtime
# with the host user's UID:GID via the user: directive.
USER detector

# Flask service port — runtime value comes from PORT env var (config.py).
EXPOSE 8080

# Start the detector.  The app reads all settings from environment / .env,
# so no CLI flags are needed for normal container operation.
CMD ["python", "app.py"]
