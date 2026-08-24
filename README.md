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
- Fail-closed custom guardrails for provider-bound PII, untrusted web-tool results, final-response PII, and protected-route client streaming. Prompt Guard 2 uses the source-pinned Hugging Face revision in `guardrails/config.py`, lossless overlapping token chunks, a finite raw-result limit, and bounded initialization, inference, and whole-result timeouts. These modules do not activate themselves; the runtime-mounted LiteLLM policy remains responsible for attaching them to the protected route in the required order.
- Cosign-signed image provenance and a multi-arch (`linux/amd64`, `linux/arm64`) build.

Except for the documented compatibility patches, the LiteLLM proxy is the same source release as `pip install litellm[proxy]==X.Y.Z`. Provider configuration, model routing, and feature flags remain upstream's surface. See [LiteLLM's docs](https://docs.litellm.ai/docs/proxy/configs) for `config.yaml` details.

## WorkOS Connect authentication

The image includes an open-source LiteLLM `custom_auth` implementation for
WorkOS Connect access tokens; no LiteLLM Enterprise licence is required. It
accepts exactly one `Authorization: Bearer` credential and validates an RS256
signature from the pinned WorkOS JWKS endpoint, exact issuer, scalar resource
audience, expiry/time claims, exact AI Alchemy organization, and a non-empty
subject. WorkOS Connect access tokens do not document client-ID or scope claims,
so this resource server deliberately does not invent or require them.

Configure the runtime-mounted LiteLLM YAML with:

```yaml
litellm_settings:
  enable_post_custom_auth_checks: true

general_settings:
  custom_auth: aialchemy_auth.runtime.workos_auth
  custom_auth_run_common_checks: true
  allow_requests_on_db_unavailable: false
```

All three settings are required. The image patch treats custom auth as an
authenticated mode even when `master_key` is absent, forces every non-metadata
MCP request through WorkOS admission, removes the WorkOS bearer before MCP
egress, and leaves only liveness probes publicly accessible. Model routes, MCP
servers/access groups, and MCP tools are explicit runtime allowlists shared by
Jarvis and Copilot.

The current shared AI Alchemy `WORKOS_ALLOWED_MODELS` catalog contains these
exact twelve routes:

```text
gemini/gemini-3.5-flash
gemini/gemini-3.5-flash-lite
gemini/gemini-3.6-flash
gemini/gemini-embedding-2
gemini/gemini-3.1-flash-image
chatgpt/gpt-5.6-sol
chatgpt/gpt-5.6-terra
chatgpt/gpt-5.6-luna
cohere/rerank-v4.0-fast
qwen/qwen3-embedding-8b
mistral/mistral-ocr-latest
mistral/mistral-ocr-4
```

Custom auth does not hardcode that catalog: it preserves the exact non-empty
comma-separated runtime allowlist. Both clients therefore receive the same
configured catalog without relying on client-ID claims, OAuth scopes, access
groups, or virtual keys.

A WorkOS-provided issuer such as `https://<project>.authkit.app` is supported;
the paid WorkOS custom-domain option is not required. The resource audience
remains the AI Alchemy LiteLLM/MCP HTTPS URL.

## Pull and run

```bash
docker run --rm \
  --env-file ./gateway.env \
  -v ./config.yaml:/app/config.yaml:ro \
  -p 4000:4000 \
  ghcr.io/aialchemylabs/aialchemy-llm-gateway:v1.97.0
```

Config is never baked into the image. Mount your `config.yaml` at
`/app/config.yaml` at runtime. `gateway.env` supplies the WorkOS settings and
provider credentials; the WorkOS-only data plane does not configure a LiteLLM
master key or issue virtual keys.

## Runtime model support

This image does not bake in a provider config. The runtime-mounted `config.yaml` is the source of truth for which models a running gateway exposes, and a provider is live-working only when its config entry, credentials, and upstream service are all present.

For the AI Alchemy local stack, the mounted config lives in `core-infra/llm-gateway-config.yml`; keep the actual provider/model report there rather than copying LiteLLM's full upstream catalog into this image repo.

### Gemini routing and streaming

The image routes an exact Gemini model through LiteLLM when the runtime-mounted
`config.yaml` includes that exact model and the container has `GEMINI_API_KEY`
set:

```yaml
model_list:
  - model_name: gemini/gemini-3.6-flash
    litellm_params:
      model: gemini/gemini-3.6-flash
      api_key: os.environ/GEMINI_API_KEY
```

When a client requests streaming, LiteLLM maps the request to Google's `streamGenerateContent?alt=sse` transport. The image build verifies this mapping with `gemini-3.6-flash`; the runtime config and Google project remain authoritative for which exact model IDs are available.

### Recommended env vars

| Var | Purpose | Notes |
|---|---|---|
| `WORKOS_ISSUER` | Exact WorkOS AuthKit issuer | May use the WorkOS-provided `*.authkit.app` domain |
| `WORKOS_JWKS_URL` | WorkOS signing keys | Must be the issuer-origin `/oauth2/jwks` URL |
| `WORKOS_AUDIENCE` | Exact LiteLLM/MCP resource indicator | Must match the token's scalar `aud` claim |
| `WORKOS_ORG_ID` | Exact AI Alchemy WorkOS organization | Required |
| `WORKOS_ALLOWED_MODELS` | Comma-separated common model allowlist | Required and non-empty |
| `WORKOS_MCP_SERVERS` | Comma-separated common MCP server allowlist | At least this or `WORKOS_MCP_ACCESS_GROUPS` is required |
| `WORKOS_MCP_ACCESS_GROUPS` | Comma-separated common MCP access groups | Optional when servers are listed directly |
| `WORKOS_MCP_TOOL_PERMISSIONS_JSON` | Server-to-tool allowlist JSON | Required and non-empty |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, … | Provider API keys referenced from `config.yaml` | Required for the providers you list |
| `DATABASE_URL` | Postgres connection string | Required for spend logs and persisted MCP registrations |
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

./scripts/run_workos_container_e2e.py --image aialchemy-llm-gateway:local
```

The `linux/arm64` build runs the `prisma generate` step under qemu emulation if you're on amd64; that step takes longer (a few minutes) than the rest of the image combined.
The container probe generates one-use local JWT/TLS material, exercises the
running proxy over HTTP, and removes its development container on exit.

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
│   ├── prompt_guard.py        # Pinned Prompt Guard 2 tokenizer and lossless chunking
│   ├── prompt_guard_client.py # Pinned fail-closed async model inference (binary labels)
│   ├── responses_tool_output.py # Strict structured tool-output parsing and rewriting
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
