# Perplexity integration plan

Status: proposed; documentation checked on 2026-09-22. This PR plans the
integration. It does not change the image, Core Infra, application permissions,
or the running deployment, and does not claim a successful live API test.

## Decision and ownership

Route application requests through the existing private LiteLLM gateway, using
its native Perplexity Agent API adapter and `POST /v1/responses`. Start with one
explicit route, `perplexity-research`, mapped to `perplexity/preset/low`.
Applications keep using server-held gateway virtual keys; the Perplexity key
belongs only in the gateway container.

| Layer | Planned work |
| --- | --- |
| `aialchemy-llm-gateway` | Document the contract and prove compatibility with the pinned LiteLLM 1.101.0 image. Add a patch or upgrade only if a reproduced failure requires it. |
| `core-infra` | Add the route, Docker secret, startup export, catalogue tests, entitlement instructions, and deployment runbook. Recreate the gateway service to apply them. |
| Consuming application | Use the approved alias and Responses endpoint; handle response items, citations, streaming, and errors. Deploy that application only if its client needs changes. |

No new Perplexity container, database migration, public ingress, or change to
the separate Manifest Gateway is expected. Keep the current immutable gateway
image digest if the compatibility gate passes. An image rebuild and digest
update are needed only if gateway code or dependencies must change.

The local Core Infra checkout currently defines these responsibilities in
`docker-compose.yml`, `llm-gateway-config.yml`,
`scripts/prepare-gateway-secrets.sh`, and `docs/litellm-entitlements.md`.
Its checked-in configuration is evidence of intended deployment, not proof of
the image or configuration currently running in production.

## Proposed request contract

Add this entry to Core Infra's existing `model_list`; do not replace the list:

```yaml
- model_name: perplexity-research
  model_info:
    mode: responses
  litellm_params:
    model: perplexity/preset/low
    api_key: os.environ/PERPLEXITY_API_KEY
```

Grant the exact alias to approved application keys. A family access group is
unnecessary for the initial single route. If a key restricts `allowed_routes`,
include the Responses route using the pinned proxy's accepted route format.
Keep all existing key scopes and budgets intact.

Example from a trusted application server, where `GATEWAY_URL` is the private
gateway origin without a trailing `/v1`:

```bash
curl "$GATEWAY_URL/v1/responses" \
  -H "Authorization: Bearer $GATEWAY_VIRTUAL_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "perplexity-research",
    "input": "Research recent developments in battery recycling and cite sources."
  }'
```

The response uses the Responses API shape, including output items and citation
annotations; a chat-only consumer needs adaptation. Test `stream: true`
separately. Do not promise transparent Chat Completions conversion, arbitrary
Perplexity endpoint forwarding, background jobs, or every new Agent API tool.

Perplexity's current LiteLLM guide lists `fast`, `low`, `medium`, `high`, and
`xhigh` presets. LiteLLM's provider page still illustrates names such as
`pro-search`. Use Perplexity's current names, and verify that the pinned adapter
forwards the selected preset correctly. Legacy Sonar via
`perplexity/sonar-pro` and `/v1/chat/completions` is a separate optional route,
outside the initial rollout.

## Implementation sequence

### 1. Prove the existing gateway image

- Record the actual deployed digest and LiteLLM version; compare them with
  Core Infra's digest pin and this repository's `requirements.txt`.
- Run an offline contract against that exact image, with mocked upstream HTTP
  and synthetic credentials. Verify that `perplexity/preset/low` selects the
  native Perplexity Responses adapter, becomes the intended upstream preset,
  and uses the configured provider key without forwarding the client key.
- Check non-streaming and SSE translation, output/citation preservation,
  upstream error handling, and token/tool usage fields. Confirm whether the
  pinned cost map can account for the actual model and search/tool charges;
  missing prices must not be silently treated as verified zero cost.
- If a check fails, reproduce it and scope a separate gateway implementation
  PR with a regression contract. Follow the existing fail-closed patch and
  upgrade process. Do not bump LiteLLM merely to add a provider route.

### 2. Prepare a companion Core Infra PR

| File | Change |
| --- | --- |
| `llm-gateway-config.yml` | Add the explicit Responses route above. |
| `docker-compose.yml` | Define `perplexity_api_key` from `./.secrets/perplexity-api-key`, mount it only into `llm-gateway`, and export `PERPLEXITY_API_KEY` from `/run/secrets/perplexity_api_key` in the existing startup wrapper. |
| `scripts/prepare-gateway-secrets.sh` | Extend the existing provider-secret migration helper for `PERPLEXITY_API_KEY`; preserve existing secret files and never print values. |
| `scripts/test-llm-gateway-model-catalog.rb` | Extend the exact catalogue and secret expectations; check Responses mode, provider route, credential reference, mount, and startup export. |
| `README.md`, `docs/llm-gateway-production.md`, `docs/service-contracts.md` | Document the route, secret provisioning, client contract, canaries, and targeted rollout. |
| `docs/litellm-entitlements.md` | Document opt-in application scope and any required Responses route permission. |

The startup wrapper export must retain Compose's escaped dollar syntax:

```sh
export PERPLEXITY_API_KEY="$$(cat /run/secrets/perplexity_api_key)"
```

Provision a funded Perplexity API account/key and a nonempty mode-0600 secret
file before recreating a service that requires the mount. Store the file in
Core Infra's existing gitignored secret directory. The preparation helper's
missing-secret warning is not sufficient deployment validation: explicitly
check existence, nonempty contents, and permissions without printing contents.

Run the catalogue test, `scripts/test-compose-network-topology.sh`, shell syntax
checks for the modified preparation script, and `docker compose config --quiet`.
The topology test also checks the Headroom overlay; confirm that it retains the
new secret and that its hook handles or safely bypasses this Responses route.
Preserve the private networks and loopback binding.

### 3. Validate through the private gateway

Use a limited canary key before granting ordinary application access:

- Prove successful authentication with the canary key and denial with no key,
  an invalid key, and a valid key without the new model entitlement.
- Make a short, non-streaming research request and a streaming request. Confirm
  the upstream preset, usable output, citations, and stream completion.
- Confirm rate-limit and provider-error behavior with mocks where practical;
  avoid deliberately exhausting real quota. Check bounded retry/timeouts.
- Inspect spend records and telemetry for model identity, token/tool usage,
  latency, and accurate cost accounting against provider usage. Resolve gaps
  before relying on LiteLLM budgets to limit Perplexity spend.
- Confirm logs and client-visible responses do not expose credentials. Run
  representative existing ChatGPT, Gemini, and TypeSafe canaries as regression
  checks after gateway recreation.

Do not enable deeper research presets until request durations, cancellation,
and the existing 600-second streaming duration / 60-second inactivity settings
have been validated for those workloads. Do not increase global limits as part
of this initial route without evidence.

### 4. Roll out and retain rollback

Capture the accepted image digest, configuration revision, and existing key
model scopes without recording credential values. Merge and deploy the Core
Infra change only after the compatibility and configuration checks pass and
the secret is provisioned. If an image change was required, publish and verify
that image first, then update Core Infra's immutable digest reference.

Apply the change using the deployment's normal Compose file set, including its
Headroom overlay when enabled. Recreate only the `llm-gateway` service with
`docker compose up -d --no-deps --force-recreate llm-gateway` (and the applicable
`-f` options before `up`). A process restart alone does not add a new secret
mount. Pull the gateway image first only if its digest changed. Allow for
in-flight requests to drain and a brief interruption on this single service.

Run the canaries, then add the exact alias and necessary route permission to
each approved application's existing key using the authenticated management
API. A catalogue addition alone does not update issued key entitlements.

For rollback, stop application use of the new alias, restore each changed key's
previous scope, restore the prior Compose/configuration revision and image
digest if changed, and recreate only the gateway. Repeat health and existing
provider canaries. Preserve the database and ChatGPT token volume; no full-stack
restart or destructive volume operation is required.

## Completion criteria and remaining evidence

The integration is complete when the pinned image passes the contract, the
Core Infra PR is deployed, an approved application succeeds through its own
virtual key with citations and streaming, unauthorized keys remain denied,
and usage/cost tracking is verified. The required account key, deployed-image
inspection, compatibility tests, live canaries, and Core Infra implementation
remain future work at the time of this planning PR.

## Sources

- [Gateway runtime configuration](../README.md#runtime-configuration)
- [LiteLLM Perplexity provider documentation](https://docs.litellm.ai/docs/providers/perplexity)
- [Perplexity's LiteLLM integration guide](https://docs.perplexity.ai/docs/getting-started/integrations/litellm)
- [Perplexity's Agent API migration overview](https://docs.perplexity.ai/docs/agent-api/migrate-from-sonar/overview)
