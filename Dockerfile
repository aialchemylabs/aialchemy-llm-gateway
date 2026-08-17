# syntax=docker/dockerfile:1.7

# Stage 1: bring in a pinned uv binary. uv is ~15 MB static and the image
# published by Astral is the supported distribution channel. Pinned to the
# exact version on the developer laptop for build parity.
FROM ghcr.io/astral-sh/uv:0.8.11 AS uv

# Stage 2: runtime image. Python 3.13 matches local dev (3.13.2).
FROM python:3.13-slim AS runtime

# curl is required by the HEALTHCHECK below; ca-certificates lets uv and
# litellm talk to upstream providers over HTTPS. nodejs is used by
# `prisma generate` (the prisma CLI is a Node binary) so the LiteLLM
# admin UI's DB-backed features (virtual keys, spend logs, user mgmt)
# work when DATABASE_URL is set. We clean apt lists in the same RUN
# layer to keep the image under the 800 MB target (NFR #3).
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates nodejs libatomic1 \
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

# LiteLLM 1.97.0's Responses health probe passes a bare string to
# `aresponses`, while the ChatGPT subscription adapter requires canonical
# list input. Apply a fail-closed source patch until upstream ships the fix.
COPY scripts/patch_litellm_health_check.py /tmp/patch_litellm_health_check.py
RUN python /tmp/patch_litellm_health_check.py \
 && rm /tmp/patch_litellm_health_check.py

# LiteLLM 1.97.0 dispatches Anthropic Messages requests to the ChatGPT
# subscription provider without first entering its completion-to-Responses
# bridge. That sends Claude Code's system prompt as an unsupported system-role
# input item. Force ChatGPT completion calls through the existing bridge, which
# preserves the prompt in the Responses `instructions` field.
COPY scripts/patch_litellm_chatgpt_responses_bridge.py /tmp/patch_litellm_chatgpt_responses_bridge.py
RUN python /tmp/patch_litellm_chatgpt_responses_bridge.py \
 && rm /tmp/patch_litellm_chatgpt_responses_bridge.py

# Claude Code sends its system prompt as structured text blocks. LiteLLM's
# generic bridge keeps structured system content as a system-role Responses
# input item, which the ChatGPT subscription backend rejects. Move text-only
# structured system blocks into `instructions` in the ChatGPT provider only.
COPY scripts/patch_litellm_chatgpt_structured_system.py /tmp/patch_litellm_chatgpt_structured_system.py
RUN python /tmp/patch_litellm_chatgpt_structured_system.py \
 && rm /tmp/patch_litellm_chatgpt_structured_system.py

# The ChatGPT subscription Responses endpoint is SSE-only. LiteLLM's generic
# fake-stream heuristic strips the provider's forced `stream: true` for newly
# released model names it does not recognize. Disable fake streaming for this
# provider so chatgpt/* automatically works with future subscription models.
COPY scripts/patch_litellm_chatgpt_native_stream.py /tmp/patch_litellm_chatgpt_native_stream.py
RUN python /tmp/patch_litellm_chatgpt_native_stream.py \
 && rm /tmp/patch_litellm_chatgpt_native_stream.py

# Dynamic chatgpt/* routes can lead LiteLLM's static capability lookup to
# silently reduce xhigh/max effort to high. Preserve the explicit client value
# and let the subscription backend validate support for each model.
COPY scripts/patch_litellm_chatgpt_reasoning_effort.py /tmp/patch_litellm_chatgpt_reasoning_effort.py
RUN python /tmp/patch_litellm_chatgpt_reasoning_effort.py \
 && rm /tmp/patch_litellm_chatgpt_reasoning_effort.py

# Lock the provider-specific streaming contract into the image build. ChatGPT
# subscription requests must keep native SSE and explicit xhigh effort even for
# model names newer than LiteLLM's registry; Gemini streaming must use
# streamGenerateContent + SSE.
COPY scripts/verify_litellm_streaming_contract.py /tmp/verify_litellm_streaming_contract.py
RUN python /tmp/verify_litellm_streaming_contract.py \
 && rm /tmp/verify_litellm_streaming_contract.py

# Smoke imports at build time. This catches upstream-extra regressions and
# verifies the callback dependency that LiteLLM imports at proxy startup.
RUN python -c "import litellm.proxy.proxy_server; import prometheus_client"

# Copy the AiAlchemy guardrails package and its tests into the image.
# scripts/ is included because the patch regression tests load each patch
# module by path to assert it is idempotent and fails closed.
COPY guardrails/ /app/guardrails/
COPY tests/ /app/tests/
COPY scripts/ /app/scripts/

# Prove the wrapper can actually receive the request dict and the selected
# guardrail. The wrapper reads both defensively, so a renamed upstream parameter
# would make it resolve the guardrail to None and skip inspection silently —
# with unit tests still passing. This gate fails the build instead.
RUN python scripts/verify_responses_guardrail_contract.py

# Verify the Responses guardrail patch can bind to the pinned LiteLLM.
#
# LiteLLM 1.97.0 skips guardrail invocation for Responses continuations that
# contain only function_call / function_call_output, because its text extraction
# finds nothing to inspect. That is the bypass must-have-requirements.md §4.3
# prohibits. This step fails the build if the handler class or method we wrap is
# absent, so an image whose tool-result path is unguarded is never published.
RUN python -m guardrails.litellm_responses_patch

# Activate the patch for every interpreter in this image. sitecustomize is
# imported automatically at startup, so the wrapper is installed before the
# proxy serves its first request — no reliance on guard-module import order.
RUN SITE_DIR="$(python -c 'import site; print(site.getsitepackages()[0])')" \
 && printf '%s\n' \
      'try:' \
      '    from guardrails.litellm_responses_patch import apply_patch' \
      '    apply_patch()' \
      'except Exception as exc:  # fail loudly, never silently unguarded' \
      '    raise RuntimeError(' \
      '        "AiAlchemy Responses guardrail patch failed to apply: %r" % (exc,)' \
      '    )' \
    > "$SITE_DIR/sitecustomize.py" \
 && python -c "import sitecustomize; print('sitecustomize: responses guardrail patch active')"

# Guardrail module smoke import — proves every guard and its deps resolve.
RUN python -c "\
from guardrails.config import WEB_TOOL_ALLOWLIST, PRESIDIO_ENTITIES; \
from guardrails.presidio_client import PresidioClient, PresidioError; \
from guardrails.prompt_guard import chunk_text, PromptGuardClassifier, ChunkLimitExceeded; \
from guardrails.prompt_guard_client import PromptGuardClient, PromptGuardError; \
from guardrails.pii_input_guard import AiAlchemyPiiInputGuard; \
from guardrails.web_tool_result_guard import AiAlchemyWebToolResultGuard; \
from guardrails.pii_output_guard import AiAlchemyPiiOutputGuard; \
from guardrails.stream_reject_guard import AiAlchemyStreamRejectGuard; \
print('guardrails: all modules imported successfully'); \
print(f'  web-tool allowlist: {sorted(WEB_TOOL_ALLOWLIST)}'); \
print(f'  presidio entities: {len(PRESIDIO_ENTITIES)} configured'); \
"

# EXECUTE the behavioural test suite. Importing the modules is not evidence —
# these tests are what prove the fail-closed contracts (PII masking reaches the
# provider, tool-only continuations are inspected, malformed classifier output
# blocks, streaming is rejected). A failure here fails the build.
RUN python -m unittest discover -s tests -p 'test_*.py' -t . -v

# Generate the Prisma client + engine binaries against the schema.prisma
# that ships inside the litellm package. Doing this at build time means
# container startup doesn't have to download Prisma engines from the CDN
# every time, and the image is reproducible.
RUN SCHEMA="$(python -c 'import os, litellm.proxy as p; print(os.path.join(os.path.dirname(p.__file__), "schema.prisma"))')" \
 && prisma generate --schema="$SCHEMA"

EXPOSE 4000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD curl -fsS http://localhost:4000/health/liveliness || exit 1

# Config is runtime-mounted at /app/config.yaml. The image never ships a
# config file — Requirements §3 (Functional) is explicit that config must
# never be baked into the image.
ENTRYPOINT ["litellm", "--config", "/app/config.yaml", "--port", "4000", "--host", "0.0.0.0"]
