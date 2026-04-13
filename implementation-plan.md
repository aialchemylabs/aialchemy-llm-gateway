AI Alchemy LLM Gateway — Implementation Plan
=============================================

*Status: ready to execute. Authored 2026-04-13 against [LiteLLM-requirement.md](LiteLLM-requirement.md).*

This plan turns the requirements doc into an ordered, executable sequence of changes. Every file referenced lives in this repository (`aialchemylabs/aialchemy-llm-gateway`). No changes are made in `legal-intelligence` as part of this plan — those are called out separately at the end as *consumer follow-ups*.

---

## 0. Decisions locked before implementation

| Decision | Value | Source |
| --- | --- | --- |
| LiteLLM version pin | `1.83.4` (released 2026-04-07, not yanked, `[proxy]` extras confirmed on PyPI) | PyPI `/pypi/litellm/1.83.4/` |
| Python base image | `python:3.13-slim` (matches local dev `python --version` = 3.13.2) | User directive 2026-04-13 |
| Package installer | `uv` 0.8.11 — pinned via `ghcr.io/astral-sh/uv:0.8.11` copy-from stage | User directive 2026-04-13 |
| Architectures | `linux/amd64` + `linux/arm64` from day 1 | User directive 2026-04-13 |
| Registry | `ghcr.io/aialchemylabs/aialchemy-llm-gateway`, visibility Public | Requirements §Image publishing |
| CI auth | default `GITHUB_TOKEN` with `packages: write` + `id-token: write` | User directive 2026-04-13 |
| Signing | cosign keyless via `sigstore/cosign-installer@v4.1.1` + GitHub OIDC | User directive 2026-04-13 |
| Tag scheme | `v1.83.4`, `v1.83.4-<git-sha>`, `latest` | Requirements §Image publishing |

`uv 0.8.11` matches the developer laptop (`uv --version` → `uv 0.8.11 (f892276ac 2025-08-14)`), keeping local and CI builds bit-identical at the resolver layer. `python:3.13-slim` matches local `python --version` (3.13.2); we accept the small image-size delta vs 3.12-slim in exchange for environment parity.

---

## 1. Repo skeleton

Create the following files at the repo root. No subdirectories besides `.github/workflows/`.

```
aialchemy-llm-gateway/
├── Dockerfile
├── requirements.txt
├── .dockerignore
├── LICENSE                       # Apache-2.0 verbatim
├── NOTICE                        # attribution + trademark disclaimer
├── README.md                     # build / push / attribution / non-affiliation
├── LiteLLM-requirement.md        # already present
├── implementation-plan.md        # this file
└── .github/
    ├── dependabot.yml
    └── workflows/
        └── image.yml
```

`.dockerignore` excludes `.git`, `.github`, `*.md`, and any stray `.env*` so secrets can't land in the build context by accident.

---

## 2. `requirements.txt`

```
# LiteLLM proxy — pinned for reproducible image builds.
# Chosen: 1.83.4 (released 2026-04-07 on PyPI).
# Rationale: latest stable release at pin time; no open high-severity CVEs;
# confirmed `[proxy]` extras published; not yanked. Revisit on every
# Dependabot PR against this file.
litellm[proxy]==1.83.4
```

Only this one line (plus comment). All transitive deps are resolved by `uv` at build time. If we later decide we need a full lockfile for deterministic transitive pins (NFR #6), we upgrade to `uv pip compile` producing `requirements.lock` — deferred until Phase 2 of the gateway roadmap unless Dependabot surfaces resolver drift.

---

## 3. `Dockerfile`

```dockerfile
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
```

Notes on choices:
- **Two-stage only for the uv binary**, not for the Python install. A full builder/runtime split would save little here because `litellm[proxy]` has no compiled extensions worth isolating, and keeping one runtime stage makes the `docker history` secret scan in CI simpler to reason about.
- **`UV_COMPILE_BYTECODE=1`** trades a small image size increase for faster cold start, helping us hit the 30 s healthcheck budget (NFR #4).
- **`--start-period=20s`** gives litellm time to bind the port before `HEALTHCHECK` starts counting failures; without it the container can be marked unhealthy during a normal warm start.
- **No `USER` directive.** LiteLLM's official deployment examples run as root inside the container and so does the upstream `berriai/litellm` image. Adding a non-root user is a worthwhile hardening but changes behavior surface area beyond the requirements doc; deferred and tracked in §9.

---

## 4. `LICENSE`, `NOTICE`, `README.md`

- **`LICENSE`** — verbatim Apache-2.0 text. Covers the repo itself; LiteLLM's own license continues to ship inside `site-packages/litellm-*.dist-info/` in the image, satisfying Requirements §Licensing.
- **`NOTICE`** — attribution block:
  > This product includes software developed by BerriAI (https://github.com/BerriAI/litellm), distributed under the Apache License 2.0. LiteLLM is a trademark of BerriAI. This project is not affiliated with or endorsed by BerriAI.
- **`README.md`** — five sections:
  1. What this image is (unmodified repackage of `litellm[proxy]` from PyPI).
  2. Why it exists (upstream GHCR tag instability — references Requirements §Background).
  3. How to pull and run (`docker run -e LITELLM_MASTER_KEY=... -v ./config.yaml:/app/config.yaml -p 4000:4000 ghcr.io/aialchemylabs/aialchemy-llm-gateway:v1.83.4`).
  4. How to build locally (`docker buildx build ...`).
  5. Attribution and non-affiliation — exact wording from Requirements §Licensing.

Only section 5 is load-bearing for compliance; the others keep consumers from having to read the requirements doc.

---

## 5. `.github/workflows/image.yml`

Single job `build-and-publish`. Triggers:
- `workflow_dispatch`
- `push` to `main` where `paths` matches `Dockerfile` or `requirements.txt`

Permissions:
```yaml
permissions:
  contents: read
  packages: write
  id-token: write   # required for cosign keyless OIDC
```

Step outline (full YAML authored during implementation):

1. **Checkout** — `actions/checkout@v4`.
2. **Extract version** — a shell step that parses `litellm[proxy]==X.Y.Z` out of `requirements.txt` into `$GITHUB_OUTPUT` as `version`. Fails hard if the line is missing or ambiguous.
3. **Set up QEMU** — `docker/setup-qemu-action@v3` for arm64 emulation.
4. **Set up Buildx** — `docker/setup-buildx-action@v3`.
5. **GHCR login** — `docker/login-action@v3` using `${{ github.actor }}` + `${{ secrets.GITHUB_TOKEN }}`.
6. **Build and push** — `docker/build-push-action@v6` with:
    - `platforms: linux/amd64,linux/arm64`
    - `tags: ghcr.io/aialchemylabs/aialchemy-llm-gateway:v${{ steps.version.outputs.version }}`, `...:v${{ version }}-${{ github.sha }}`, `...:latest`
    - `provenance: true`, `sbom: true`
    - `outputs: type=image,push=true`
    - Emit `digest` to step outputs.
7. **Install cosign** — `sigstore/cosign-installer@v4.1.1`.
8. **Sign** — `cosign sign --yes ghcr.io/aialchemylabs/aialchemy-llm-gateway@${{ steps.build.outputs.digest }}`. Signing by digest, not tag, so the signature is immutable.
9. **Secret scan** — shell step that runs `docker buildx imagetools inspect` + `docker history --no-trunc` on the pulled image and greps for `API_KEY|SECRET|TOKEN|PASSWORD` (case-insensitive). Fails the job on any match. Runs *after* push but before the job ends — a positive match is treated as a critical incident and triggers manual package deletion in GHCR.
10. **Summary** — append `### Published\n\`ghcr.io/...@<digest>\`` to `$GITHUB_STEP_SUMMARY` for reviewer reference.

The secret scan runs against the just-pushed image rather than the local buildx cache because multi-arch builds under buildx don't leave a single local image to `docker history` — pulling the manifest back from GHCR is the simplest way to inspect both arches. This is acceptable because the image was already uploaded; the mitigation if the scan fails is to delete the package and rotate any leaked key.

### Why not run the scan *before* push
Buildx multi-arch builds emit an OCI manifest list that can't be inspected by `docker history` without loading each platform back in. The `imagetools inspect --raw` path post-push is the cleanest option. The offset risk (image is briefly published before scan passes) is mitigated by (a) never using the workflow on anything except `main`, (b) no secrets ever being in the Dockerfile by design, and (c) the scan being a belt-and-braces check on top of the Dockerfile review.

---

## 6. `.github/dependabot.yml`

```yaml
version: 2
updates:
  - package-ecosystem: pip
    directory: /
    schedule:
      interval: weekly
    open-pull-requests-limit: 5
    commit-message:
      prefix: "chore(deps)"
  - package-ecosystem: github-actions
    directory: /
    schedule:
      interval: weekly
    commit-message:
      prefix: "chore(ci)"
```

Pip ecosystem watches `requirements.txt` for new LiteLLM releases (Requirements §Security obligations). GitHub Actions ecosystem keeps `docker/*-action`, `sigstore/cosign-installer`, and `actions/checkout` current.

---

## 7. Local build and verification (before CI exists)

Executed on this laptop as the final step of implementation, before the first push:

1. **Build amd64 (native)** to prove the Dockerfile is correct quickly:
   ```bash
   docker buildx build --platform linux/amd64 --load -t aialchemy-llm-gateway:local .
   ```
2. **Inspect size** — `docker image ls aialchemy-llm-gateway:local`. Must be < 800 MB (NFR #3).
3. **History scan** — `docker history --no-trunc aialchemy-llm-gateway:local | grep -Ei 'api_key|secret|token|password'`. Must return nothing.
4. **Smoke-run** against a throwaway config:
   ```bash
   cat > /tmp/llm-gateway-smoke.yaml <<'YAML'
   model_list:
     - model_name: gpt-4o-mini
       litellm_params:
         model: openai/gpt-4o-mini
         api_key: os.environ/OPENAI_API_KEY
   general_settings:
     master_key: os.environ/LITELLM_MASTER_KEY
   YAML
   docker run --rm -d --name llm-smoke \
     -e LITELLM_MASTER_KEY=sk-smoke \
     -e OPENAI_API_KEY=sk-placeholder \
     -v /tmp/llm-gateway-smoke.yaml:/app/config.yaml:ro \
     -p 4000:4000 \
     aialchemy-llm-gateway:local
   ```
5. **Healthcheck poll** — wait up to 30 s for `curl -fsS http://localhost:4000/health/liveliness` to return 200. Exit criterion per Requirements §Validation #5.
6. **Authenticated `/v1/models` probe** — `curl -sS -H "Authorization: Bearer sk-smoke" http://localhost:4000/v1/models | jq '.data[].id'` must list `gpt-4o-mini`. Proves config loading + bearer auth.
7. **Teardown** — `docker rm -f llm-smoke` and delete `/tmp/llm-gateway-smoke.yaml`.
8. **Multi-arch cross-build dry run** (no push) to verify the buildx graph for arm64 resolves:
   ```bash
   docker buildx build --platform linux/amd64,linux/arm64 .
   ```
   This is an unauthenticated, no-push build; it just confirms arm64 layers resolve so CI won't surprise us. Skipped only if it takes > 15 minutes locally, in which case we trust CI.

If any of 1–6 fail, halt and fix before proceeding. No partial commits.

---

## 8. Execution order

Step 1 through 6 are file authoring. Everything is staged locally first, then committed in two commits so the history tells a coherent story:

1. Commit A — `LICENSE`, `NOTICE`, `.dockerignore`, `requirements.txt`, `Dockerfile`, `README.md`. Core image is buildable and documented.
2. **Local verification (§7).** If green, continue; if red, fix in place and amend before committing CI.
3. Commit B — `.github/workflows/image.yml`, `.github/dependabot.yml`. CI and automation.
4. Push `main`. First CI run validates multi-arch build, secret scan, and cosign signing.
5. **Manual GHCR visibility flip to Public** (Requirements §First-publish visibility flip). This step is outside the workflow because GHCR's package-visibility API is not exposed to `GITHUB_TOKEN` and requires a user-scoped PAT or the UI.
6. **Anonymous pull verification** — from a machine or `docker logout`ed shell: `docker pull ghcr.io/aialchemylabs/aialchemy-llm-gateway:v1.83.4`. Satisfies Requirements §Validation #4.
7. **Cosign verify** — `cosign verify --certificate-identity-regexp 'https://github.com/aialchemylabs/aialchemy-llm-gateway/.github/workflows/image.yml@.*' --certificate-oidc-issuer https://token.actions.githubusercontent.com ghcr.io/aialchemylabs/aialchemy-llm-gateway:v1.83.4`. Satisfies NFR #8.

---

## 9. Explicitly deferred (out of scope for v1)

- **Non-root `USER` in the Dockerfile.** Worth doing; not in requirements; defer to a follow-up PR.
- **`uv pip compile` lockfile for transitive pins.** Current plan pins only the top-level `litellm[proxy]`. Upgrade if Dependabot shows transitive drift causing resolver instability.
- **SBOM attestation beyond what `docker/build-push-action` emits by default.** `sbom: true` in the build step produces one; a richer Syft-based attestation can come later.
- **Trivy / Grype scan in CI.** Adds value but isn't in the requirements and costs CI minutes. Add in Phase 2 if the org adopts a formal vuln-scanning policy.
- **Rollback automation.** Requirements §Open questions asks for rollback *documentation* on the consumer side, not automation. Covered in the consumer follow-up below.

---

## 10. Consumer follow-ups (tracked for `legal-intelligence`, not executed here)

These are the acceptance criteria from Requirements §Validation #6 and #7 that live in the consumer repo. Listed here so we don't lose them:

1. Update `infra/docker-compose.yml` — replace `ghcr.io/berriai/litellm:main-latest` with `ghcr.io/aialchemylabs/aialchemy-llm-gateway:v1.83.4`. In production, pin by digest.
2. Update `infra/README.md` — point at the new image, note the distribution source and how to verify cosign signatures.
3. Re-run Phase 0 QA: `pnpm --filter @aialchemylabs/rag exec tsx /tmp/rag-phase0-smoke/live.ts` against the new compose environment; confirm `rag.healthcheck()` returns `status: 'ok'`.
4. Document the rollback procedure: revert `infra/docker-compose.yml` to the previous pinned digest and `docker compose up -d litellm` — no code changes required.
5. Remove `draft` status from `LiteLLM-requirement.md` once all validation items pass.

---

## 11. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Upstream `litellm[proxy]` version published but transitive dep resolution shifts between our local build and CI | `uv` produces deterministic resolution for a given input; if we see drift, upgrade to `uv pip compile` lockfile (deferred item). |
| `cosign-installer@v4.1.1` changes behavior on a major bump | Dependabot on GitHub Actions ecosystem surfaces bumps; review before merging. |
| Multi-arch CI doubles build time beyond acceptable | Fallback in Requirements §Open questions is to publish amd64 first. Monitor first run; tune if > 15 min. |
| `curl` in the slim image becomes a CVE magnet | It's only used by HEALTHCHECK. If it becomes a liability, replace with a tiny Python one-liner (`python -c "import urllib.request; urllib.request.urlopen('http://localhost:4000/health/liveliness').read()"`). |
| GHCR silently reverts the package to private | Documented manual step after first publish; re-verify during consumer follow-up #3 (anonymous pull). |

---

## 12. Done criteria for this plan

This plan is complete when:

- All files in §1 exist in the repo.
- Local verification §7 steps 1–6 pass.
- First CI run publishes `v1.83.4`, `v1.83.4-<sha>`, and `latest` as a multi-arch signed image.
- `docker pull` from an anonymous context succeeds.
- `cosign verify` against the published digest succeeds.

At that point the remaining work is the consumer follow-ups in §10, which belong in `legal-intelligence`.
