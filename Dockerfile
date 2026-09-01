# syntax=docker/dockerfile:1.7

# Pin the build tooling and LiteLLM Python release instead of inheriting from
# the floating upstream LiteLLM container image.
FROM ghcr.io/astral-sh/uv:0.8.11 AS uv

FROM python:3.13-slim AS runtime

ARG IMAGE_VERSION=unknown
ARG VCS_REF=unknown

LABEL org.opencontainers.image.title="AI Alchemy LLM Gateway" \
      org.opencontainers.image.description="Multi-architecture LiteLLM gateway with AI Alchemy compatibility patches" \
      org.opencontainers.image.source="https://github.com/aialchemylabs/aialchemy-llm-gateway" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="$IMAGE_VERSION" \
      org.opencontainers.image.revision="$VCS_REF"

# curl supports the container health check. Node.js and libatomic1 support the
# Prisma client used by LiteLLM's database-backed proxy features.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates nodejs libatomic1 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /usr/local/bin/

WORKDIR /app

COPY requirements.txt /app/requirements.txt

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN uv pip install --system --no-cache -r /app/requirements.txt

# LiteLLM 1.97.0's Responses health probe passes a bare string to
# `aresponses`, while the ChatGPT subscription adapter requires canonical list
# input. Keep working subscription routes healthy in the Admin UI.
COPY scripts/patch_litellm_health_check.py /tmp/patch_litellm_health_check.py
RUN python /tmp/patch_litellm_health_check.py \
 && rm /tmp/patch_litellm_health_check.py

# Force ChatGPT completion calls through LiteLLM's Responses bridge so
# Claude Code's Anthropic-shaped requests preserve system instructions.
COPY scripts/patch_litellm_chatgpt_responses_bridge.py /tmp/patch_litellm_chatgpt_responses_bridge.py
RUN python /tmp/patch_litellm_chatgpt_responses_bridge.py \
 && rm /tmp/patch_litellm_chatgpt_responses_bridge.py

# Move text-only structured system blocks into Responses `instructions` for
# the ChatGPT subscription provider.
COPY scripts/patch_litellm_chatgpt_structured_system.py /tmp/patch_litellm_chatgpt_structured_system.py
RUN python /tmp/patch_litellm_chatgpt_structured_system.py \
 && rm /tmp/patch_litellm_chatgpt_structured_system.py

# Preserve JSON-schema structured-output configuration through ChatGPT
# subscription provider dispatch.
COPY scripts/patch_litellm_chatgpt_structured_output.py /tmp/patch_litellm_chatgpt_structured_output.py
RUN python /tmp/patch_litellm_chatgpt_structured_output.py \
 && rm /tmp/patch_litellm_chatgpt_structured_output.py

# The ChatGPT subscription Responses endpoint is SSE-only. Disable LiteLLM's
# fake-stream fallback for this provider, including future model names.
COPY scripts/patch_litellm_chatgpt_native_stream.py /tmp/patch_litellm_chatgpt_native_stream.py
RUN python /tmp/patch_litellm_chatgpt_native_stream.py \
 && rm /tmp/patch_litellm_chatgpt_native_stream.py

# Buffer provider-required SSE when the client requested a non-streaming
# response, allowing LiteLLM to return a complete converted response.
COPY scripts/patch_litellm_chatgpt_internal_sse_buffering.py /tmp/patch_litellm_chatgpt_internal_sse_buffering.py
RUN python /tmp/patch_litellm_chatgpt_internal_sse_buffering.py \
 && rm /tmp/patch_litellm_chatgpt_internal_sse_buffering.py

# Retain completed output_item.done events when converting a ChatGPT
# subscription SSE response into a non-streaming response.
COPY scripts/patch_litellm_responses_bridge_sse_aggregation.py /tmp/patch_litellm_responses_bridge_sse_aggregation.py
RUN python /tmp/patch_litellm_responses_bridge_sse_aggregation.py \
 && rm /tmp/patch_litellm_responses_bridge_sse_aggregation.py

# Preserve explicit xhigh/max effort on dynamic ChatGPT model names and let
# the provider validate whether the selected model supports it.
COPY scripts/patch_litellm_chatgpt_reasoning_effort.py /tmp/patch_litellm_chatgpt_reasoning_effort.py
RUN python /tmp/patch_litellm_chatgpt_reasoning_effort.py \
 && rm /tmp/patch_litellm_chatgpt_reasoning_effort.py

# Verify ChatGPT subscription and Gemini streaming translations against the
# installed, already-patched LiteLLM package.
COPY scripts/verify_litellm_streaming_contract.py /tmp/verify_litellm_streaming_contract.py
RUN python /tmp/verify_litellm_streaming_contract.py \
 && rm /tmp/verify_litellm_streaming_contract.py

# Route `huggingface/<provider>/<org>/<model>` embedding requests through the
# named Hugging Face inference provider instead of always using hf-inference.
COPY scripts/patch_litellm_huggingface_embedding_provider_routing.py /tmp/patch_litellm_huggingface_embedding_provider_routing.py
RUN python /tmp/patch_litellm_huggingface_embedding_provider_routing.py \
 && rm /tmp/patch_litellm_huggingface_embedding_provider_routing.py

RUN python -c "import litellm.proxy.proxy_server; import prometheus_client"

# Retained scripts and tests cover only the pinned LiteLLM compatibility
# patches included above.
COPY scripts/ /app/scripts/
COPY tests/ /app/tests/

RUN python scripts/verify_huggingface_embedding_provider_routing_contract.py

# Prove LiteLLM's Claude subscription OAuth pass-through against the installed,
# patched package: the gateway virtual key is never forwarded, a recognized
# sk-ant-oat Authorization is retained and Anthropic-scoped, the OAuth value is
# redacted from logging, and no server-side Anthropic key is required.
RUN python scripts/verify_claude_oauth_passthrough_contract.py

RUN python -m unittest discover -s tests -p 'test_*.py' -t . -v

# Bake the Prisma engine binaries into the image for database-backed virtual
# keys, spend logs, teams, and Admin UI functionality.
RUN SCHEMA="$(python -c 'import os, litellm.proxy as p; print(os.path.join(os.path.dirname(p.__file__), "schema.prisma"))')" \
 && prisma generate --schema="$SCHEMA"

EXPOSE 4000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD curl -fsS http://localhost:4000/health/liveliness || exit 1

# Runtime configuration and credentials are mounted/injected by private
# deployment infrastructure. Nothing deployment-specific is baked in.
ENTRYPOINT ["litellm", "--config", "/app/config.yaml", "--port", "4000", "--host", "0.0.0.0"]
