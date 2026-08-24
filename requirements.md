# WorkOS authentication requirements for the AIAlchemy LiteLLM gateway

## Objective

Make WorkOS the only end-user authentication authority for LiteLLM-backed
model and MCP traffic. The gateway must accept WorkOS access tokens from
Jarvis WebUI and Microsoft Copilot, validate the same claim contract for both
clients, and grant the same model and MCP permissions after successful
validation.

This is a greenfield authentication contract. It does not preserve virtual-key
compatibility, Jarvis-issued JWTs, Portkey authentication, Hermes routing, or
different permission tiers for Jarvis and Copilot.

## Repository responsibility

This repository owns:

- the pinned LiteLLM wrapper image;
- the WorkOS `custom_auth` implementation and its dependencies;
- any compatibility patch required for LiteLLM's model and MCP routes to use
  the same validated identity;
- any image-level support required for an external MCP authorization server;
- unit, integration, and image-build tests for the authentication contract.

This repository does not own:

- WorkOS, Google Cloud, or Microsoft Copilot tenant configuration;
- production issuer, audience, organization, permission, or secret values;
- Docker Compose, Cloudflare, DNS, or production deployment configuration;
- Jarvis login, session storage, or outbound token propagation;
- upstream MCP tool implementations.

Production configuration and deployment belong to `core-infra`. Jarvis token
acquisition and forwarding belong to `jarvis-webui`.

## Authentication contract

### Accepted credential

Protected model and MCP data-plane routes must accept a WorkOS access token as
an HTTP bearer credential. Missing, malformed, unverifiable, expired, or
insufficient tokens must fail closed before provider dispatch or MCP tool
execution.

No protected request may fall back to a LiteLLM virtual key, a Jarvis-signed
JWT, an identity header, anonymous access, or a Portkey credential.

Operational control-plane and break-glass authentication are outside this
user-token contract. They must remain isolated from end-user routes and must
not create a fallback that admits model or MCP traffic.

### Required validation

The custom authentication handler must validate all of the following:

- JWT signature against WorkOS-published JWKS;
- an explicit asymmetric algorithm allowlist;
- exact configured issuer;
- exact configured resource audience;
- `exp` and, when present, `nbf` and `iat` time constraints with a bounded
  clock-skew allowance;
- exact configured `org_id` for AIAlchemy;
- a non-empty WorkOS subject (`sub`).

WorkOS Connect's documented access-token JWT does not contain `client_id`,
`azp`, or `scope`. The gateway must not invent those claims, reinterpret `aud`
as a client identifier, or require undocumented claims that would reject valid
tokens. WorkOS OAuth client registration controls which clients can obtain a
token; the exact resource audience controls where that token can be used.

JWKS retrieval must use HTTPS, support WorkOS signing-key rotation, cache keys
for a bounded period, and fail closed when an unknown key cannot be refreshed.
Tokens, authorization codes, client secrets, and raw authorization headers
must never be logged.

### Identity and authorization mapping

After successful validation, the handler must return a LiteLLM
`UserAPIKeyAuth` identity derived from immutable WorkOS claims. The stable
WorkOS subject is the user identifier; `org_id`, issuer, and audience remain
available as trusted request metadata for audit and policy checks.

Jarvis and Microsoft Copilot must map to the same model allowlist, MCP server
allowlist, MCP tool permissions, budgets, and rate-limit policy. The gateway
must not attempt to infer the calling OAuth client from the resource audience.

Model access and MCP object permissions must be explicit and testable. A
successful JWT validation alone must not silently grant unrestricted gateway
access.

The shared AI Alchemy WorkOS model catalog is the following exact runtime
allowlist. Both Jarvis and Copilot receive all twelve entries; the custom-auth
implementation must preserve the configured values generically rather than
embedding provider- or model-specific authorization branches:

- `gemini/gemini-3.5-flash`;
- `gemini/gemini-3.5-flash-lite`;
- `gemini/gemini-3.6-flash`;
- `gemini/gemini-embedding-2`;
- `gemini/gemini-3.1-flash-image`;
- `chatgpt/gpt-5.6-sol`;
- `chatgpt/gpt-5.6-terra`;
- `chatgpt/gpt-5.6-luna`;
- `cohere/rerank-v4.0-fast`;
- `qwen/qwen3-embedding-8b`;
- `mistral/mistral-ocr-latest`;
- `mistral/mistral-ocr-4`.

The list is supplied through `WORKOS_ALLOWED_MODELS`; no model entry is a
virtual key, access group, OAuth scope, or OAuth client identifier.

### Route coverage

The WorkOS contract must cover every LiteLLM-backed path used by Jarvis and
Copilot, including:

- chat completions;
- Responses API;
- embeddings and retrieval-related inference;
- reranking and OCR;
- image generation;
- task-model requests;
- model discovery used by Jarvis;
- aggregate and named MCP discovery, initialization, tool listing, and tool
  execution routes.

Public health checks and standards-required OAuth discovery documents may
remain unauthenticated. No model catalogue, MCP server inventory, tool schema,
or execution endpoint may become public as a side effect.

## MCP OAuth discovery

The public MCP protected-resource metadata must be capable of identifying the
configured WorkOS AuthKit domain as its authorization server and the canonical
LiteLLM MCP URL as its resource audience. The response contract is:

The issuer may use the default WorkOS-hosted `*.authkit.app` domain. A paid
custom AuthKit domain is optional branding and is not an authentication or
release requirement.

```json
{
  "resource": "<WORKOS_MCP_RESOURCE>",
  "authorization_servers": ["<WORKOS_ISSUER>"],
  "bearer_methods_supported": ["header"]
}
```

`core-infra` may publish this document at ingress instead of patching LiteLLM,
provided the gateway's `401` challenge points to that canonical document and
end-to-end discovery succeeds. If the pinned LiteLLM build cannot support that
contract through configuration or ingress, the smallest fail-closed
compatibility patch belongs in this repository.

The WorkOS token must be consumed only as LiteLLM admission credential. It
must not be forwarded to an internal MCP server unless that server is
explicitly designed and configured as the same WorkOS resource.

## Configuration interface

Runtime values must be supplied by environment or secret-backed configuration,
not committed literals. The implementation must define and document the
following logical settings:

- `WORKOS_ISSUER`;
- `WORKOS_JWKS_URL`, unless obtained safely from issuer metadata;
- `WORKOS_AUDIENCE`;
- `WORKOS_ORG_ID`;
- `WORKOS_ALLOWED_MODELS`;
- `WORKOS_MCP_SERVERS` and/or `WORKOS_MCP_ACCESS_GROUPS`;
- `WORKOS_MCP_TOOL_PERMISSIONS_JSON`;
- bounded JWKS cache and clock-skew settings.

The runtime LiteLLM configuration must bind
`aialchemy_auth.runtime.workos_auth`, enable both post-custom-auth and common
authorization checks, and fail closed on database unavailability. Empty model,
route, server, or tool permission collections must not be treated as a safe
default because LiteLLM interprets several empty collections as unrestricted.

The issuer may be the WorkOS-provided `*.authkit.app` domain. A paid custom
WorkOS domain is not required; the resource audience remains the canonical AI
Alchemy LiteLLM/MCP HTTPS URL.

Startup must reject absent, empty, wildcard, malformed, insecure, or internally
inconsistent production values. Diagnostic output may report presence and
configuration shape but not credential values.

## Project structure and code style

- Authentication implementation belongs in a dedicated gateway module, not in
  individual model handlers or guardrails.
- Compatibility patches belong under `scripts/` and must be deterministic and
  idempotent.
- Authentication tests belong under `tests/` and must execute during the image
  build.
- Existing guardrail behavior remains independent of authentication and must
  continue to fail closed.

Use typed, narrow functions with explicit claim inputs and denial results. For
example:

```python
def validate_workos_claims(claims: WorkOSClaims, settings: WorkOSAuthSettings) -> Principal:
    if claims.org_id != settings.org_id:
        raise AuthenticationDenied("organization_not_allowed")
    if not claims.sub:
        raise AuthenticationDenied("subject_required")
    return Principal(user_id=claims.sub, organization_id=claims.org_id)
```

Errors returned to clients must be stable and non-sensitive. Detailed internal
reasons may be logged only as bounded reason codes without token or claim dumps.

## Commands

Run from the repository root:

```bash
python -m unittest discover -s tests -p 'test_*.py' -t . -v
docker build --progress=plain -t aialchemy-llm-gateway:workos-auth .
./scripts/run_workos_container_e2e.py --image aialchemy-llm-gateway:workos-auth
```

The Docker build is the authoritative image-level gate because it installs the
pinned LiteLLM version, applies compatibility patches, and executes the bundled
test suite. The disposable container probe is the HTTP-level gate for bearer
admission plus chat, embedding, rerank, and OCR dispatch; it requires no real
WorkOS or provider credentials and removes its development container on exit.

## Testing strategy

Tests must include:

- valid tokens obtained through each approved client producing the same
  authorization result;
- wrong signature, algorithm, issuer, audience, organization, and missing
  subject;
- expired and not-yet-valid tokens;
- JWKS cache hit, signing-key rotation, refresh failure, and unknown key ID;
- missing and malformed bearer headers;
- denial before provider dispatch and before MCP execution;
- model discovery, chat, Responses, embeddings, images, task models, and MCP
  route coverage;
- no virtual-key, Jarvis-JWT, Portkey, identity-header, or anonymous fallback;
- no admission-token forwarding to internal MCP servers;
- unchanged fail-closed guardrail behavior.

At least one integration test must prove that equivalent Jarvis and Copilot
tokens receive the same model and MCP authorization object. HTTP success alone
is insufficient: tests must prove model invocation and MCP tool execution with
safe fixtures, plus negative denials.

## Boundaries

### Always

- Fail closed on authentication, JWKS, configuration, or claim ambiguity.
- Validate the full WorkOS claim contract before authorization mapping.
- Give Jarvis and Copilot the same permissions.
- Keep authentication separate from guardrails and provider credentials.
- Redact all bearer tokens and secrets from logs and test output.

### Ask first

- Changing the pinned LiteLLM version.
- Adding a new token issuer, organization, audience, or permission set.
- Changing control-plane or break-glass authentication.
- Forwarding a WorkOS token to an upstream MCP server.

### Never

- Reintroduce Hermes, Portkey, Jarvis-signed JWTs, or virtual-key fallback.
- Accept unsigned tokens, symmetric algorithms, wildcard issuers/audiences, or
  caller-supplied identity headers.
- Infer or differentiate permissions by Jarvis versus Copilot client identity.
- Log credentials or weaken existing guardrails to make authentication pass.

## Success criteria

- Every listed model and MCP data-plane route authenticates with WorkOS.
- Invalid claims are rejected before external side effects.
- Jarvis and Copilot produce identical authorized permissions.
- No legacy authentication fallback is reachable.
- MCP clients discover WorkOS as the authorization server and obtain a token
  with the canonical LiteLLM resource audience.
- All unit, integration, patch, and Docker image tests pass.

## Required external inputs

Implementation may use placeholders until the user supplies:

- WorkOS issuer/AuthKit domain and JWKS metadata;
- canonical LiteLLM resource audience;
- AIAlchemy WorkOS organization ID;
- the common model and MCP permission set.

No production value or secret may be guessed.
