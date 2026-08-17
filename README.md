# AI Alchemy LLM Gateway

A multi-arch Docker image that wraps `litellm[proxy]` with the runtime dependencies needed for the proxy's metrics, tracing, and database-backed features to work out of the box. Published to GitHub Container Registry under our org namespace.

## Why this image exists

`pip install litellm[proxy]` installs LiteLLM but not every dependency its callbacks reach for at runtime. Three specific gaps:

- `litellm_settings.callbacks: ["otel"]` raises `ModuleNotFoundError: No module named 'opentelemetry'` — the `[proxy]` extra does not pull `opentelemetry-api` / `sdk` / OTLP exporter.
- `litellm_settings.callbacks: ["prometheus"]` raises `ModuleNotFoundError: No module named 'prometheus_client'` — the `[proxy]` extra does not pull the Prometheus client.
- `DATABASE_URL=<postgres>` raises `ModuleNotFoundError: No module named 'prisma'` and, even with the Python client installed, fails with "Unable to find Prisma binaries. Please run 'prisma generate' first." — `[proxy]` does not pull `prisma`, the Prisma CLI is a Node binary, and the engine binaries need to be generated against LiteLLM's bundled `schema.prisma`.

This image takes the upstream `litellm[proxy]` release and adds:

- `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http` (1.31.x line, the lowest line whose `importlib-metadata` range coexists with LiteLLM's pin).
- `prometheus-client==0.20.0` for LiteLLM's authenticated `/metrics` endpoint and spend/key/team/budget series.
- `prisma==0.11.0` plus `nodejs` + `libatomic1` in the runtime image, with `prisma generate --schema=<litellm>/proxy/schema.prisma` baked into a build step so engine binaries ship in the image (no first-start CDN download).
- Build-time smoke imports of `litellm.proxy.proxy_server` and `prometheus_client` to catch upstream-extra and callback regressions before the image is published.
- A narrow, fail-closed LiteLLM 1.97.0 compatibility patch that sends Responses health probes as a one-item input list. This keeps working ChatGPT subscription routes from being reported unhealthy by the Admin UI.
- Four fail-closed Claude Code compatibility patches for the ChatGPT subscription provider: completion-shaped calls enter LiteLLM's existing Responses bridge, text-only structured system blocks become Responses `instructions`, LiteLLM's fake-stream fallback is disabled for this SSE-only provider, and explicit `xhigh`/`max` reasoning effort is preserved on dynamic model names. Together these preserve Claude Code's system prompt, keep `stream: true`, and prevent silent effort downgrades on new `chatgpt/*` model names that are not yet present in LiteLLM's model registry. Remove each patch when the pinned upstream release contains equivalent behavior.
- A build-time streaming contract check proving that ChatGPT subscription requests retain native SSE and `xhigh` effort, while Gemini streaming resolves to Google's `streamGenerateContent?alt=sse` transport.
- Cosign-signed image provenance and a multi-arch (`linux/amd64`, `linux/arm64`) build.

Except for the documented compatibility patches, the LiteLLM proxy is the same source release as `pip install litellm[proxy]==X.Y.Z`. Provider configuration, model routing, and feature flags remain upstream's surface. See [LiteLLM's docs](https://docs.litellm.ai/docs/proxy/configs) for `config.yaml` details.

## Pull and run

```bash
docker run --rm \
  -e LITELLM_MASTER_KEY=sk-your-key \
  -e OPENAI_API_KEY=sk-your-openai-key \
  -v ./config.yaml:/app/config.yaml:ro \
  -p 4000:4000 \
  ghcr.io/aialchemylabs/aialchemy-llm-gateway:v1.97.0
```

Config is never baked into the image. Mount your `config.yaml` at `/app/config.yaml` at runtime. All provider API keys are injected via environment variables.

## Runtime model support

This image does not bake in a provider config. The runtime-mounted `config.yaml` is the source of truth for which models a running gateway exposes, and a provider is live-working only when its config entry, credentials, and upstream service are all present.

For the AI Alchemy local stack, the mounted config lives in `core-infra/llm-gateway-config.yml`; keep the actual provider/model report there rather than copying LiteLLM's full upstream catalog into this image repo.

### Gemini routing and streaming

The image routes an exact Gemini model through LiteLLM when the runtime-mounted `config.yaml` includes that model or a `gemini/*` wildcard and the container has `GEMINI_API_KEY` set:

```yaml
model_list:
  - model_name: gemini/*
    litellm_params:
      model: gemini/*
      api_key: os.environ/GEMINI_API_KEY
```

When a client requests streaming, LiteLLM maps the request to Google's `streamGenerateContent?alt=sse` transport. The image build verifies this mapping with `gemini-3.6-flash`; the runtime config and Google project remain authoritative for which exact model IDs are available.

### Recommended env vars

| Var | Purpose | Notes |
|---|---|---|
| `LITELLM_MASTER_KEY` | Auth header for every gateway call + admin UI login | Required |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, … | Provider API keys referenced from `config.yaml` | Required for the providers you list |
| `DATABASE_URL` | Postgres connection string | Required for spend logs, virtual keys, admin UI auth |
| `STORE_MODEL_IN_DB` | `"True"` to allow runtime model edits via the UI | Requires `DATABASE_URL` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP collector URL | e.g. `http://otel-collector:4318` |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | **`otlp_http`** or `otlp_grpc` | LiteLLM treats this as the exporter NAME, **not** the OTEL-spec wire format. Setting it to `http/protobuf` (the OTEL standard value) silently disables the exporter. |
| `OTEL_SERVICE_NAME` | Service name shown in your tracing UI | e.g. `litellm` |

Add `litellm_settings.callbacks: ["otel"]` in `config.yaml` to actually emit spans — env vars alone aren't enough.

## Image versioning

| Tag | What it is |
|---|---|
| `vX.Y.Z` | The pinned `litellm[proxy]==X.Y.Z` release |
| `vX.Y.Z-<sha>` | The same release built from a specific commit of this repo |
| `latest` | Floating tag — most recent build, whatever release that was |

Tags are mutable when this repo bumps `requirements.txt` to a new patch line — pull by digest if you need byte-for-byte reproducibility.

## Build locally

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --tag aialchemy-llm-gateway:local \
  --load \
  .
```

The `linux/arm64` build runs the `prisma generate` step under qemu emulation if you're on amd64; that step takes longer (a few minutes) than the rest of the image combined.

## Verify provenance

```bash
cosign verify \
  --certificate-identity-regexp 'https://github.com/aialchemylabs/aialchemy-llm-gateway/.github/workflows/image.yml@.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/aialchemylabs/aialchemy-llm-gateway:v1.97.0
```

The CI workflow signs every published image and SBOM, and scans both architectures' layers for secret-shaped strings before the build is allowed to succeed.

## Bumping LiteLLM

1. Edit `requirements.txt` — change `litellm[proxy]==X.Y.Z` to the new release.
2. If LiteLLM has changed its OTEL or Prisma client requirements, update those pins too.
3. Push to `main` — CI extracts the version from `requirements.txt`, builds, signs, and publishes `vX.Y.Z`, `vX.Y.Z-<sha>`, and `latest`.

CI only triggers on changes to `Dockerfile` or `requirements.txt`. README / docs / workflow tweaks won't rebuild the image.

## Repo layout

```
.
├── Dockerfile                 # python:3.13-slim + uv + nodejs + prisma generate + healthcheck
├── requirements.txt           # litellm[proxy], prometheus, otel, prisma, torch, transformers — pinned
├── guardrails/                # AiAlchemy custom LiteLLM guardrails (aialchemy-global-baseline-v1)
│   ├── config.py              # Version-controlled thresholds, allowlists, entity lists
│   ├── presidio_client.py     # Async HTTP client for self-hosted Presidio services
│   ├── prompt_guard.py        # Prompt Guard 2 86M token chunking and classification
│   ├── prompt_guard_client.py # Fail-closed async inference wrapper (binary labels)
│   ├── pii_input_guard.py     # Step 1: mask PII in provider-bound content
│   ├── web_tool_result_guard.py # Step 2: inspect untrusted web-tool results
│   ├── pii_output_guard.py    # Step 3: mask PII in final user-visible responses
│   ├── stream_reject_guard.py # Reject client streaming on the protected route
│   └── litellm_responses_patch.py # Guarantee guardrail invocation for tool-only continuations
├── scripts/                   # Build-time source patches and contract verifications
├── tests/                     # Behavioural test suite — executed in the image build
├── docs/                      # License obligations and design documents
├── .github/workflows/image.yml # CI: build, sign with cosign, secret-scan, publish
├── LICENSE                    # Apache 2.0
├── NOTICE                     # Attribution to upstream LiteLLM
└── README.md
```

## Attribution and non-affiliation

This image bundles the upstream `litellm[proxy]` release with a small set of additional runtime dependencies (`prometheus-client`, `opentelemetry-*`, `prisma`, `nodejs`) so the proxy's metrics, tracing, and database features work out of the box. The narrow, fail-closed compatibility patches applied to the pinned LiteLLM source are documented above and in the Dockerfile.

LiteLLM is a trademark of BerriAI; this project is not affiliated with or endorsed by BerriAI. See `NOTICE` for full attribution.
