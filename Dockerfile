# syntax=docker/dockerfile:1.7

# Stage 1: bring in a pinned uv binary. uv is ~15 MB static and the image
# published by Astral is the supported distribution channel. Pinned to the
# exact version on the developer laptop for build parity.
FROM ghcr.io/astral-sh/uv:0.8.11 AS uv

# Stage 2: runtime image. Python 3.13 matches local dev (3.13.2).
FROM python:3.13-slim AS runtime

# curl is required by the HEALTHCHECK below; ca-certificates lets uv and
# litellm talk to upstream providers over HTTPS. We clean apt lists in the
# same RUN layer to keep the image under the 800 MB target (NFR #3).
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Copy the pinned uv binary from the uv stage. No pip install of uv — we
# want the exact binary Astral shipped.
COPY --from=uv /uv /uvx /usr/local/bin/

WORKDIR /app

COPY requirements.txt /app/requirements.txt

# --system installs into the base image's site-packages. --no-cache keeps
# layer size down. UV_LINK_MODE=copy avoids hardlink warnings on the slim
# base where /tmp and site-packages are on the same fs but uv's default
# mode emits noise.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN uv pip install --system --no-cache -r /app/requirements.txt

# Smoke import at build time. Catches the "upstream silently drops [proxy]"
# failure mode called out in Requirements §Open questions.
RUN python -c "import litellm.proxy.proxy_server"

EXPOSE 4000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD curl -fsS http://localhost:4000/health/liveliness || exit 1

# Config is runtime-mounted at /app/config.yaml. The image never ships a
# config file — Requirements §3 (Functional) is explicit that config must
# never be baked into the image.
ENTRYPOINT ["litellm", "--config", "/app/config.yaml", "--port", "4000", "--host", "0.0.0.0"]
