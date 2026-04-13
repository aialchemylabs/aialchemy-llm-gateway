# AI Alchemy LLM Gateway — Service Requirements

*Status: draft. Created 2026-04-13 after Phase 0 of the RAG SDK migration surfaced that `ghcr.io/berriai/litellm:main-latest` pulls are unreliable.*

> **Implementation repo**: these requirements are satisfied by a separate repository, `aialchemylabs/aialchemy-llm-gateway`. **Do not implement the Dockerfile, CI workflow, or image publishing in `legal-intelligence`.** This file lives here as the consumer-side record of what Legal Intelligence requires from that gateway, and what this repo must change once the image is published.

## Purpose

Define what the AI Alchemy LLM gateway service must be, so a clean Docker image can be built from the official `litellm[proxy]` pip package, published to our GitHub Container Registry as a public image under `ghcr.io/aialchemylabs/aialchemy-llm-gateway`, and consumed by Legal Intelligence (and any future AI Alchemy project) without depending on the unstable upstream `ghcr.io/berriai/litellm:main-latest` tag.

The gateway itself is unmodified LiteLLM, repackaged from its PyPI release. The purpose of the new repo is **distribution control**, not forking upstream.

## Background

- The rag-sdk requires an OpenAI-compatible HTTP endpoint for embeddings and answer generation (`RagConfig.embeddings.baseUrl`, `RagConfig.answering.baseUrl`). It does not accept a TypeScript-level provider injection, so a TypeScript abstraction like `@aialchemylabs/agentic-utils` cannot be plugged into the rag-sdk directly.
- Platform LLM calls outside the rag-sdk (classification, structured extraction, drafting) use `@aialchemylabs/agentic-utils` at the TypeScript level and do **not** need this gateway.
- The existing `infra/docker-compose.yml` points at `ghcr.io/berriai/litellm:main-latest`. Phase 0 QA confirmed that tag returns `denied` on anonymous pulls. `main-stable` and pinned upstream tags also break intermittently because the upstream repo uses GHCR's package visibility inconsistently.
- LiteLLM is also available as a Python package (`pip install 'litellm[proxy]'`), which lets us build our own container from a controlled base image and pin the version deterministically.

Decision: package LiteLLM ourselves from the pip distribution in a **new standalone repo**, publish to **our** GHCR namespace, and have Legal Intelligence consume the pinned image tag.

## Scope

### In scope for the new `aialchemy-llm-gateway` repo

- A Dockerfile that installs a pinned `litellm[proxy]` version on a minimal Python base image
- A locked dependency manifest (`requirements.txt` with an exact `litellm[proxy]==X.Y.Z` pin)
- A GitHub Actions workflow that builds multi-arch (amd64 + arm64) and pushes to GHCR as a public image
- A CI secret-scan that inspects `docker history` for secret-shaped strings and fails the build if any are found
- Keyless cosign signing of the published digest via GitHub Actions OIDC
- A `README.md` in the new repo documenting build, push, and attribution
- Setting the published package visibility to **Public** on GHCR

### In scope for `legal-intelligence` (this repo)

- Updating `infra/docker-compose.yml` to replace the upstream image reference with the new pinned tag (or digest) once the first image is published
- Updating `infra/README.md` to point at the new image
- Re-running the Phase 0 QA smoke test against the new compose environment

### Out of scope

- Implementing the Dockerfile, `requirements.txt`, CI workflow, or any image-build code **in `legal-intelligence`**. That work lives entirely in `aialchemy-llm-gateway`.
- Forking or patching LiteLLM source code
- Adding routes, middleware, or plugins beyond what upstream LiteLLM supports
- Replacing `@aialchemylabs/agentic-utils` for non-RAG LLM calls
- Any authentication mechanism beyond LiteLLM's built-in `LITELLM_MASTER_KEY`
- Model-list or routing changes — `infra/litellm-config.yaml` stays in `legal-intelligence` and is mounted into the container at runtime

## Functional requirements (the gateway service)

1. Expose an OpenAI-compatible HTTP API on port `4000`.
2. Serve at minimum: `/v1/chat/completions`, `/v1/embeddings`, `/v1/models`, `/health/liveliness`.
3. Load its routing config from a runtime-mounted `config.yaml` at `/app/config.yaml`. **Config must never be baked into the image.**
4. Resolve all provider API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `MISTRAL_API_KEY`, plus `LITELLM_MASTER_KEY`) from environment variables at runtime, matching the `os.environ/*` references already in `infra/litellm-config.yaml`.
5. Support LiteLLM's native model routing, retries, timeouts, and fallback chains as defined in `infra/litellm-config.yaml` (`router_settings.fallbacks`).
6. Authenticate callers via the `LITELLM_MASTER_KEY` bearer token.
7. Reach Ollama running on the host at `http://host.docker.internal:11434` when `ollama/*` models are requested. Requires `extra_hosts: host.docker.internal:host-gateway` in the compose block (already present in `legal-intelligence`).

## Non-functional requirements

1. **Reproducibility** — pin LiteLLM to an exact version (no `latest`, no `main`). Record the pinned version in both the Dockerfile and the image tag.
2. **Multi-arch** — build `amd64` and `arm64` in the same workflow so the same tag works on Intel Linux CI and Apple Silicon dev laptops.
3. **Image size** — target under 800 MB. Use `python:3.12-slim` as the base, not `python:3.12`.
4. **Cold start** — container must pass its `HEALTHCHECK` within 30 s on a warm pull.
5. **No secrets in image layers** — Dockerfile must not `COPY` any `.env`, `api_key`, or `*_KEY` file. Verified by a CI step that runs `docker history --no-trunc` and greps for secret-shaped strings.
6. **Deterministic rebuilds** — pip dependency resolution must be locked via `requirements.txt` or `pyproject.toml` + lockfile, not an unconstrained `pip install`.
7. **Health gating** — `legal-intelligence`'s compose file must use `condition: service_healthy` for services that depend on the gateway (already the case).
8. **Signed provenance** — every published image must be signed with cosign keyless signing so consumers can verify origin.

## Architecture

### Runtime

Python process inside a minimal container:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir 'litellm[proxy]==<PINNED>'
EXPOSE 4000
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
  CMD curl -fsS http://localhost:4000/health/liveliness || exit 1
ENTRYPOINT ["litellm", "--config", "/app/config.yaml", "--port", "4000", "--host", "0.0.0.0"]
```

The `<PINNED>` version must be resolved against LiteLLM's PyPI release history and recorded with a root-level comment in the Dockerfile explaining why that version was chosen.

### New repo layout (`aialchemylabs/aialchemy-llm-gateway`)

```
aialchemy-llm-gateway/
├── Dockerfile
├── requirements.txt          # exact pin: litellm[proxy]==X.Y.Z
├── README.md                 # build + push + attribution
├── LICENSE                   # Apache-2.0, same as upstream LiteLLM
├── NOTICE                    # attribution to BerriAI / LiteLLM
├── .dockerignore
└── .github/
    └── workflows/
        └── image.yml         # multi-arch build, cosign sign, push
```

The existing `infra/litellm-config.yaml` **stays in `legal-intelligence`** and is mounted into the container at runtime via the compose volume declaration. It is not copied into the new repo.

### Image publishing

- **Registry**: `ghcr.io/aialchemylabs/aialchemy-llm-gateway`
- **Visibility**: public (see "GHCR public-image considerations" below)
- **Tags**:
  - `vX.Y.Z` — immutable, matches the pinned LiteLLM version
  - `vX.Y.Z-<git-sha>` — immutable, full provenance
  - `latest` — mutable, moves with the most recent tagged build
- **Build trigger** (in the new repo's GitHub Actions):
  - manual `workflow_dispatch`
  - push to `main` when `Dockerfile` or `requirements.txt` changes
- **Builder**: `docker/build-push-action@v6` with `docker/setup-buildx-action` for multi-arch
- **Signer**: `cosign sign --yes` with GitHub Actions OIDC (keyless)

### Compose integration (in `legal-intelligence`)

Once the first image is published, replace the upstream image reference in `infra/docker-compose.yml`:

```yaml
  litellm:
    image: ghcr.io/aialchemylabs/aialchemy-llm-gateway:vX.Y.Z
    # rest of the block unchanged — env, volume mount, healthcheck, extra_hosts
```

Pin by the immutable version tag in compose, and in production environments pin by digest (`@sha256:...`) instead.

The service name inside `legal-intelligence`'s compose file stays `litellm` for now so nothing else needs to change; the service **name** is just a local identifier and does not have to match the image or package name.

## GHCR public-image considerations

### Licensing

- LiteLLM is **Apache License 2.0**. That license explicitly permits:
  - redistributing the unmodified package
  - redistributing modified versions
  - creating and distributing derivative works, including container images
  - commercial use
- Obligations the image must satisfy:
  - Preserve the LiteLLM `LICENSE` and any `NOTICE` files. These are pulled into `site-packages` automatically by `pip install` and are not removed by the slim base image, so compliance is automatic unless the Dockerfile deliberately strips them.
  - Commit copies of `LICENSE` (Apache 2.0) and a `NOTICE` file attributing LiteLLM / BerriAI at the root of the new repo.
  - Do not use the LiteLLM name or logo in a way that implies official endorsement. **The image and repo are named `aialchemy-llm-gateway` deliberately** — there is no `litellm` in the package or image name. `README.md` must state plainly: *"This image is an unmodified repackage of the upstream `litellm[proxy]` pip release. LiteLLM is a trademark of BerriAI; this project is not affiliated with or endorsed by BerriAI."*
- Apache 2.0 does not grant trademark rights, so avoid claiming any affiliation with BerriAI.

### Practical / operational

- **Storage and bandwidth**: Public GHCR images on public repos do not count against storage or bandwidth quotas for the pusher. Anonymous pulls are free. Verify against current GitHub policy before committing — GitHub has revised quotas before.
- **Discovery**: A public image is searchable and anyone on the internet can pull it. That is the intended behavior; it is also how this project stops depending on upstream tag availability.
- **Rate limits**: Public GHCR pulls are not subject to the same anonymous rate limits as Docker Hub. This is one of the reasons to publish here rather than Docker Hub.

### Security obligations when publishing publicly

- **No secrets in image layers.** The Dockerfile must not `COPY` a `.env`, a pre-filled `config.yaml`, a `.netrc`, or any file containing a key. All credentials arrive via `docker run -e` / `compose environment:` at runtime.
- **Verify with `docker history --no-trunc <image>` before the first push**, and add a CI step that fails the build if any layer command contains `API_KEY`, `SECRET`, `TOKEN`, or `PASSWORD`.
- **Sign the image.** Use `cosign sign` with GitHub Actions OIDC (keyless signing) so downstream consumers (including this repo's compose file) can verify provenance.
- **Pin by digest in production.** `image: ghcr.io/aialchemylabs/aialchemy-llm-gateway@sha256:...` in compose eliminates the risk of a tag being moved underneath a deployed environment.
- **Enable Dependabot (or Renovate) on `requirements.txt`** in the new repo so the pinned LiteLLM version stays fresh against upstream security advisories.

### Risks specific to publishing publicly

- **You become a supply-chain entry point.** Anyone on the internet who finds the image can pull it. As long as no secrets or proprietary config are inside, the risk is nil — the image contains only what is already on PyPI.
- **Trademark drift.** If LiteLLM renames or restructures its pip distribution, a future rebuild might silently pull something different. The version pin plus signed tags mitigate this.
- **Reputational / brand.** Publishing under `aialchemylabs` associates the org's name with the image. If we ever want to unlist it, GHCR supports deleting packages but historical digests may remain cached on consumers.

**Verdict**: publish public. The benefits (reliable pulls, version control, no Docker Hub rate limits, keyless signing) outweigh the minimal obligations, and the licensing is clean provided the trademark rule is followed.

## Configuration

### Runtime environment variables (injected by `legal-intelligence`'s docker-compose)

| Variable | Required | Purpose |
| --- | --- | --- |
| `LITELLM_MASTER_KEY` | Yes | Bearer token that authenticates callers. Matches `LITELLM_API_KEY` in `infra/.env`. |
| `OPENAI_API_KEY` | Yes (for GPT routes) | Forwarded to upstream OpenAI. |
| `ANTHROPIC_API_KEY` | Optional (for Claude fallback) | Forwarded to upstream Anthropic. |
| `MISTRAL_API_KEY` | Yes (for OCR fallback routed through LiteLLM) | The rag-sdk calls Mistral directly, so this is only needed if a non-SDK caller routes OCR here. |

### Mounted files

| Host path (in `legal-intelligence`) | Container path | Purpose |
| --- | --- | --- |
| `./litellm-config.yaml` | `/app/config.yaml` | Model routing, fallbacks, retries. |

### Ports

| Host | Container | Purpose |
| --- | --- | --- |
| `${LITELLM_PORT:-4000}` | `4000` | OpenAI-compatible HTTP API. |

## Build and publish process (runs in `aialchemy-llm-gateway`, not here)

### Local build (developer laptop)

```bash
cd aialchemy-llm-gateway
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --tag ghcr.io/aialchemylabs/aialchemy-llm-gateway:vX.Y.Z \
  --tag ghcr.io/aialchemylabs/aialchemy-llm-gateway:latest \
  --push \
  .
```

### CI build (GitHub Actions, `.github/workflows/image.yml` in the new repo)

Required steps:

1. Check out the repo.
2. Set up QEMU and Buildx for multi-arch.
3. Log in to GHCR using the workflow's `GITHUB_TOKEN` with `packages: write` permission.
4. Compute the tag from `requirements.txt` (extract the pinned version).
5. Build and push with `docker/build-push-action@v6`, platforms `linux/amd64,linux/arm64`.
6. Sign the published digest with `cosign sign --yes`.
7. Fail the job if `docker history --no-trunc` matches any of: `API_KEY`, `SECRET`, `TOKEN`, `PASSWORD`.
8. Emit the digest to the run summary for reviewer reference.

### First-publish visibility flip

After the first push, navigate to the package settings in GHCR and flip visibility to **Public**. GHCR packages default to private; visibility is sticky once set.

## Deployment (consumer side — `legal-intelligence`)

### Local development

1. Pull the image: `docker compose pull litellm` (uses the pinned tag in `infra/docker-compose.yml`).
2. Start: `docker compose up -d litellm`.
3. Verify: `curl -fsS http://localhost:4000/health/liveliness` returns `{"status":"healthy"}`.
4. Smoke test: `curl -sS -H "Authorization: Bearer $LITELLM_API_KEY" http://localhost:4000/v1/models | jq '.data[].id'`.

### Production (future)

- Pin by digest, not tag
- Verify cosign signature before starting
- Run behind the same network boundary as the rag-sdk worker and the Next.js app

## Validation and exit criteria

The gateway service and this distribution chain are considered ready when **all** of the following are true:

1. The new repo `aialchemylabs/aialchemy-llm-gateway` exists, builds a multi-arch image under 800 MB, and its CI passes end-to-end: build, secret-scan, push, sign, verify.
2. The first image is published to `ghcr.io/aialchemylabs/aialchemy-llm-gateway` with visibility set to **Public**.
3. `docker history` on the published image shows no secret material in any layer.
4. The image can be pulled anonymously from a machine not logged in to GitHub.
5. `docker compose up -d litellm` on a freshly cloned `legal-intelligence` (pointing at the new image tag) starts the service and passes its healthcheck within 30 s.
6. `@aialchemylabs/rag`'s Phase 0 QA smoke test passes against the new compose environment — `rag.healthcheck()` returns `status: 'ok'` and no Qdrant/client-version warning is emitted.
7. `infra/docker-compose.yml` and `infra/README.md` in `legal-intelligence` are updated with the new image reference.

## Open questions and risks

- **Exact LiteLLM version to pin.** Pick the latest stable version on PyPI with no open high-severity CVEs. Record the decision in `requirements.txt` with a comment.
- **Multi-arch CI cost.** If multi-arch doubles CI time unacceptably, publish `amd64` first and treat `arm64` as a developer convenience.
- **Cosign keyless signing requires Sigstore Fulcio.** Confirm this is acceptable to the organization's supply-chain policy before committing the workflow.
- **Rollback plan.** If the custom image ever fails after a version bump, `legal-intelligence`'s compose file must allow reverting to a previous digest without code changes. Document this in the Phase 1 cutover notes.
- **Upstream deprecation.** If LiteLLM ever stops publishing `[proxy]` to PyPI, the build breaks silently. Mitigation: Dependabot alerts + a short smoke test in CI that imports `litellm.proxy.proxy_server`.

## Next actions

### In the new `aialchemy-llm-gateway` repo (NOT here)

1. Create the repo at `github.com/aialchemylabs/aialchemy-llm-gateway` with Apache-2.0 LICENSE and a NOTICE file attributing BerriAI / LiteLLM.
2. Author `Dockerfile`, `requirements.txt` (with the pinned version), and `.dockerignore`.
3. Build and push a first image manually to verify the GHCR flow end-to-end before wiring CI.
4. Flip package visibility to **Public** in GHCR.
5. Add the GitHub Actions workflow for multi-arch build, secret scan, push, and cosign signing.
6. Enable Dependabot on `requirements.txt`.
7. Document build / publish / attribution in the new repo's `README.md`.

### In `legal-intelligence` (this repo — consumer only)

1. Wait for the first `aialchemy-llm-gateway` image tag to be published publicly.
2. Update `infra/docker-compose.yml`:
   - replace `image: ghcr.io/berriai/litellm:main-latest`
   - with `image: ghcr.io/aialchemylabs/aialchemy-llm-gateway:vX.Y.Z` (or pin by digest in production)
3. Update `infra/README.md` to point at the new image reference and explain the distribution source.
4. Re-run the Phase 0 QA smoke test (`pnpm --filter @aialchemylabs/rag exec tsx /tmp/rag-phase0-smoke/live.ts`) against the new compose environment and confirm `rag.healthcheck()` returns `status: 'ok'`.
5. Remove this document's draft status once all of the above are complete and cross-link the new repo in `infra/README.md`.
