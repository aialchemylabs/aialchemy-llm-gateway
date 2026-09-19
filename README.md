# AI Alchemy LLM Gateway

A multi-architecture LiteLLM proxy image built from a pinned Python package
release. The project deliberately avoids the floating upstream LiteLLM Docker
image so upgrades happen only when `requirements.txt` is reviewed and changed.

## Current image contract

The image currently pins `litellm[proxy]==1.101.0` on `python:3.13-slim` and
adds the runtime dependencies needed for metrics, tracing, and database-backed
proxy features.

It includes a narrow set of version-specific compatibility patches required by
AI Alchemy workloads:

- ChatGPT subscription Responses health checks.
- Claude Code/Anthropic-shaped requests through the ChatGPT Responses bridge.
- Structured system instructions and structured-output schemas for ChatGPT.
- Provider-required ChatGPT SSE, non-streaming response buffering, and completed
  SSE item aggregation.
- Explicit `xhigh` and `max` reasoning effort on dynamic ChatGPT model names.
- Named Hugging Face inference-provider routing for
  `huggingface/<provider>/<org>/<model>` embeddings.
- TypeSafe Jev native pass-through, backported from upstream
  [#41607](https://github.com/BerriAI/litellm/pull/41607) and
  [#41723](https://github.com/BerriAI/litellm/pull/41723). These changes landed
  after the 1.101.0 stable release; remove the backport once the pinned release
  provides equivalent behavior and its contract checks pass.

Every retained source patch is fail-closed at image build time and has a
regression test. When the pinned LiteLLM source no longer matches a patch's
expected shape, the image build fails instead of publishing an unknown runtime.

The image contains no WorkOS integration and no AI Alchemy custom guardrails.

## Private network boundary

LiteLLM is private infrastructure. It must be reachable only by approved
applications on the backend network and must not be exposed through a public
Cloudflare Tunnel or other public ingress.

The image includes LiteLLM's Admin UI as a supported private administrative
surface. Deployment infrastructure may enable it with
`DISABLE_ADMIN_UI=false` only when the listener remains loopback-bound or
backend-only. Administrators authenticate with the runtime-mounted master key;
the key must never be delivered to application browsers or public ingress.

Jarvis and other trusted applications authenticate with server-held LiteLLM
virtual keys. Jarvis may use one virtual key for its approved model catalogue;
each other application should receive a separate key for independent rotation,
budgets, and revocation. Keys must never be delivered to browsers.

Microsoft Copilot does not use this LiteLLM endpoint. Its public MCP ingress and
Microsoft Entra authentication belong to the separate
`aialchemy-mcp-servers` deployment.

## Runtime configuration

Provider routes, model aliases, credentials, master keys, virtual keys,
database settings, and callbacks are not included in this repository. The
deployment mounts `config.yaml` at `/app/config.yaml` and injects secrets at
runtime.

Example:

```bash
docker run --rm \
  -e LITELLM_MASTER_KEY=sk-replace-at-runtime \
  -v ./config.yaml:/app/config.yaml:ro \
  -p 127.0.0.1:4000:4000 \
  ghcr.io/aialchemylabs/aialchemy-llm-gateway:v1.101.0
```

The loopback bind in this example is intentional. Production networking is
owned by deployment infrastructure and must keep the service private.

## Included runtime dependencies

- FastAPI is resolved through LiteLLM's dependencies; the old 1.97.0
  compatibility pin is no longer required.
- `prometheus-client==0.20.0`: Prometheus callback support.
- OpenTelemetry API, SDK, and OTLP HTTP exporter `1.31.1`: tracing support.
- `prisma==0.11.0`, Node.js, and `libatomic1`: database-backed LiteLLM
  functionality with Prisma engine generation performed at build time.

These dependencies are pinned with LiteLLM and must be reviewed together during
an upgrade.

## TypeSafe through the gateway

Deployment infrastructure must inject `TYPESAFE_API_KEY` into the gateway at
runtime. Applications use their server-held LiteLLM virtual key to call
`POST /typesafe/v1/systemone` with TypeSafe's native `state`, `model`, and
`questions` body. Set a TypeSafe SDK's base URL to
`http://<private-gateway>:4000/typesafe`; `GET /typesafe/v1/models` supports
model discovery. Keep the provider key in the gateway, never in a browser.

Jev returns typed choices, scores and probabilities. The pass-through preserves
that response and records input/output usage and registry-priced cost under
the returned model version. It does not expose Jev as a `/chat/completions`
model, and does not provide streaming or end-user tracking. See the
[upstream integration documentation](https://docs.litellm.ai/docs/pass_through/typesafe).

The backport's offline contract verifies request/response forwarding,
authentication, provider credential isolation and usage/cost handling using
synthetic inputs. A real TypeSafe canary still requires a runtime provider key.

## Building locally

```bash
docker build \
  --tag aialchemy-llm-gateway:local \
  .
```

The Docker build executes the retained patch regression suite and contract
checks. A successful build proves source compatibility with the pinned package;
it does not prove provider credentials, quota, runtime routing, or semantic
responses in a deployed environment.

## Image publication

Pull requests build both architectures and execute the image's compatibility
contracts without publishing an image or receiving provider credentials.

CI publishes signed multi-architecture images for `linux/amd64` and
`linux/arm64` with provenance, an SBOM, and OCI `version` and `revision`
labels. CI verifies both labels on every published platform image.

| Tag | Meaning |
|---|---|
| `vX.Y.Z` | Pinned LiteLLM Python package version. |
| `vX.Y.Z-<sha>` | The same version built from a specific repository commit. |
| `latest` | Most recently published build from this repository. |

For reproducible deployment, pin the published image digest.

## Upgrading LiteLLM

1. Change the exact `litellm[proxy]==X.Y.Z` pin in `requirements.txt`.
2. Review the FastAPI, Prometheus, OpenTelemetry, and Prisma pins.
3. Build the image. Every retained patch and contract check must pass against
   the new source.
4. Prove ChatGPT subscription chat, Responses, streaming/non-streaming,
   structured output, reasoning effort, Hugging Face embedding routing and
   TypeSafe forwarding, authentication and usage/cost handling.
5. Validate database migrations against a disposable database. The 1.101.0
   upgrade changes budget and shadow-evaluation tables; Prisma client
   generation alone does not validate a database upgrade. Back up the deployed
   database before rollout and retain the previous image digest.
6. Publish and deploy only after those semantic canaries pass. Runtime rollout
   and provider secrets belong to Core Infra; this repository's PR does not
   upgrade the running gateway.

Do not replace the pinned Python build with the floating upstream LiteLLM
Docker image.

## Repository layout

```text
Dockerfile          Pinned Python/uv build and compatibility patch application
requirements.txt    Exact LiteLLM and operational dependency pins
scripts/            Retained source patches and image contract checks
tests/              Regression tests for retained patches
.github/workflows/  Multi-architecture build, signing, and publication
```

## Attribution and non-affiliation

LiteLLM is developed by BerriAI and distributed under the Apache License 2.0.
This project is not affiliated with or endorsed by BerriAI. See [NOTICE](NOTICE).
