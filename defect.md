# Defect: LiteLLM Admin UI is disabled in the local gateway deployment

## Status

Resolved on 2026-08-25.

## Summary

The LiteLLM gateway responds on `http://localhost:4000`, but
`http://localhost:4000/ui` redirects to the login route and then displays
`Admin UI Disabled` instead of the master-key login form. The Admin UI is an
intended private administrative surface and should be available on the local,
loopback-bound deployment.

## Observed on 2026-08-25

- Running image:
  `ghcr.io/aialchemylabs/aialchemy-llm-gateway@sha256:550a3435954ce32b281a4313f18b12c6bc800d6e899aac5675853601436302a1`
- Container: `ai-alchemy-litellm`
- Listener: `127.0.0.1:4000`
- `GET /ui` redirects to `/ui/login/`.
- The login route displays `Admin UI Disabled` and says to set
  `DISABLE_ADMIN_UI=False`.
- The running container has `DISABLE_ADMIN_UI=true`.
- The mounted master key is valid for the management API: authenticated
  `GET /key/list`, `GET /v1/models`, and `GET /model/info` all return HTTP 200.

This separates the defect from master-key validity: the UI never presents a
login form, so the key is not being rejected by the UI.

## Deployment ownership found during reproduction

The image is built by this repository, while the running container is composed
from `core-infra/docker-compose.yml`. At reproduction time that deployment set
`DISABLE_ADMIN_UI: "true"`, and its model-catalog contract test asserted that
value. The fix therefore needed an explicit image/deployment contract for the
private Admin UI, with the deployment change applied in Core Infra.

## Expected behaviour

On the loopback-bound/private backend deployment:

1. `http://localhost:4000/ui` presents the LiteLLM login form.
2. The mounted `LITELLM_MASTER_KEY` authenticates successfully.
3. Admins can list models and manage virtual keys through the UI.
4. The UI remains unavailable through public ingress and virtual keys are
   never delivered to browsers.

## Acceptance criteria

- Define the private Admin UI as a supported gateway capability.
- Set `DISABLE_ADMIN_UI=false` for the local/private deployment and update the
  contradictory deployment contract test.
- Prove the listener remains loopback-only locally and backend-only in
  production.
- Prove master-key login in a browser without logging or displaying the key.
- Prove model listing and virtual-key management in the browser.
- Add a regression check that fails when the private UI is disabled or when it
  becomes reachable through public ingress.

## Security notes

Enabling the UI must not broaden the gateway network boundary. The Admin UI
must remain private, require the master key or an approved administrative
identity flow, and never be exposed through a public Cloudflare Tunnel or
other public ingress.

## Resolution

Core Infra commit `067f98f` sets `DISABLE_ADMIN_UI=false`, and its existing
gateway contract requires the private Admin UI to remain enabled. The deployment
documentation defines `http://127.0.0.1:4000/ui/` as the private administrative
surface.

The gateway was recreated without replacing its image or deleting any volume.
Post-change verification confirmed:

- the container is healthy with zero restarts;
- port 4000 is published only on `127.0.0.1`;
- UI discovery reports `admin_ui_disabled=false`;
- `GET /ui/` returns HTTP 200;
- unauthenticated `GET /v1/models` returns HTTP 401; and
- authenticated `GET /key/list` returns HTTP 200 using the mounted master key,
  without displaying or logging the key.

Browser login verification and new regression coverage were explicitly waived
for this repair and were not performed or added.
