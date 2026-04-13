# AI Alchemy LLM Gateway

An unmodified repackage of the upstream `litellm[proxy]` pip release as a
multi-arch Docker image, published to GitHub Container Registry.

## Why this image exists

The upstream `ghcr.io/berriai/litellm:main-latest` tag suffers from
intermittent access failures — the upstream repository's GHCR package
visibility is inconsistent, and anonymous pulls are periodically denied.
This image provides a stable, version-pinned, publicly pullable alternative
under our own GHCR namespace with cosign-signed provenance.

## Pull and run

```bash
docker run --rm \
  -e LITELLM_MASTER_KEY=sk-your-key \
  -e OPENAI_API_KEY=sk-your-openai-key \
  -v ./config.yaml:/app/config.yaml:ro \
  -p 4000:4000 \
  ghcr.io/aialchemylabs/aialchemy-llm-gateway:v1.83.4
```

Config is never baked into the image. Mount your `config.yaml` at
`/app/config.yaml` at runtime. All provider API keys are injected via
environment variables.

## Build locally

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --tag aialchemy-llm-gateway:local \
  --load \
  .
```

## Verify provenance

```bash
cosign verify \
  --certificate-identity-regexp 'https://github.com/aialchemylabs/aialchemy-llm-gateway/.github/workflows/image.yml@.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/aialchemylabs/aialchemy-llm-gateway:v1.83.4
```

## Attribution and non-affiliation

This image is an unmodified repackage of the upstream `litellm[proxy]`
pip release. LiteLLM is a trademark of BerriAI; this project is not
affiliated with or endorsed by BerriAI. See `NOTICE` for full attribution.
