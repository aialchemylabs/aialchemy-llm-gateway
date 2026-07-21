# AI Alchemy LLM Gateway

A multi-arch Docker image that wraps `litellm[proxy]` with the runtime dependencies needed for the proxy's tracing and database-backed features to work out of the box. Published to GitHub Container Registry under our org namespace.

## Why this image exists

`pip install litellm[proxy]` installs LiteLLM but not the dependencies its callbacks reach for at runtime. Two specific gaps:

- `litellm_settings.callbacks: ["otel"]` raises `ModuleNotFoundError: No module named 'opentelemetry'` — the `[proxy]` extra does not pull `opentelemetry-api` / `sdk` / OTLP exporter.
- `DATABASE_URL=<postgres>` raises `ModuleNotFoundError: No module named 'prisma'` and, even with the Python client installed, fails with "Unable to find Prisma binaries. Please run 'prisma generate' first." — `[proxy]` does not pull `prisma`, the Prisma CLI is a Node binary, and the engine binaries need to be generated against LiteLLM's bundled `schema.prisma`.

This image takes the upstream `litellm[proxy]` release and adds:

- `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http` (1.31.x line, the lowest line whose `importlib-metadata` range coexists with LiteLLM's pin).
- `prisma==0.11.0` plus `nodejs` + `libatomic1` in the runtime image, with `prisma generate --schema=<litellm>/proxy/schema.prisma` baked into a build step so engine binaries ship in the image (no first-start CDN download).
- A build-time smoke import of `litellm.proxy.proxy_server` to catch upstream-extra regressions before the image is published.
- Cosign-signed image provenance and a multi-arch (`linux/amd64`, `linux/arm64`) build.

The LiteLLM proxy itself is unmodified — same source release as `pip install litellm[proxy]==X.Y.Z`. Provider configuration, model routing, and feature flags are entirely upstream's surface. See [LiteLLM's docs](https://docs.litellm.ai/docs/proxy/configs) for `config.yaml` details.

## Pull and run

```bash
docker run --rm \
  -e LITELLM_MASTER_KEY=sk-your-key \
  -e OPENAI_API_KEY=sk-your-openai-key \
  -v ./config.yaml:/app/config.yaml:ro \
  -p 4000:4000 \
  ghcr.io/aialchemylabs/aialchemy-llm-gateway:v1.93.0
```

Config is never baked into the image. Mount your `config.yaml` at `/app/config.yaml` at runtime. All provider API keys are injected via environment variables.

## Runtime model support

This image does not bake in a provider config. The runtime-mounted `config.yaml` is the source of truth for which models a running gateway exposes, and a provider is live-working only when its config entry, credentials, and upstream service are all present.

For the AI Alchemy local stack, the mounted config lives in `core-infra/llm-gateway-config.yml`; keep the actual provider/model report there rather than copying LiteLLM's full upstream catalog into this image repo.

### Gemini 3.5 Flash routing

The image can route Gemini 3.5 Flash through LiteLLM when the runtime-mounted `config.yaml` includes the model and the container has `GEMINI_API_KEY` set:

```yaml
model_list:
  - model_name: gemini/gemini-3.5-flash
    litellm_params:
      model: gemini/gemini-3.5-flash
      api_key: os.environ/GEMINI_API_KEY
```

For long-context compression workloads, prefer this stable model name when it is available from Google AI Studio. `gemini/gemini-3-flash-preview` can remain as a fallback for environments that have not yet enabled the stable 3.5 route.

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
  ghcr.io/aialchemylabs/aialchemy-llm-gateway:v1.93.0
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
├── requirements.txt           # litellm[proxy], opentelemetry-*, prisma — all pinned
├── .github/workflows/image.yml # CI: build, sign with cosign, secret-scan, publish
├── LICENSE                    # Apache 2.0
├── NOTICE                     # Attribution to upstream LiteLLM
└── README.md
```

## Attribution and non-affiliation

This image bundles the upstream `litellm[proxy]` release with a small set of additional runtime dependencies (`opentelemetry-*`, `prisma`, `nodejs`) so the proxy's tracing and database features work out of the box. The LiteLLM source itself is unmodified.

LiteLLM is a trademark of BerriAI; this project is not affiliated with or endorsed by BerriAI. See `NOTICE` for full attribution.
