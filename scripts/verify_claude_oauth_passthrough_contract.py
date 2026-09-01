#!/usr/bin/env python3
"""Prove LiteLLM 1.99.0 Claude subscription OAuth pass-through behavior.

Claude Code (Max/Pro subscription) sends two credentials to the gateway:

* ``x-litellm-api-key: Bearer <litellm-virtual-key>`` — authenticates the
  request *with LiteLLM* (budget, rate limits, spend tracking); and
* ``Authorization: Bearer sk-ant-oat...`` — the Anthropic subscription OAuth
  token, which must reach the Anthropic API so the caller's own subscription
  is billed. No server-side ``ANTHROPIC_API_KEY`` is involved.

This build-time contract runs against the real installed (and patched) litellm
package and asserts the exact fail-closed properties Core Infra relies on:

1. ``x-litellm-api-key`` authenticates with LiteLLM and is NEVER forwarded
   upstream.
2. A recognized ``sk-ant-oat*`` ``Authorization`` value is retained for
   Anthropic dispatch.
3. That OAuth value is scoped to the Anthropic provider only (never attached
   to a non-Anthropic provider request).
4. The OAuth value is redacted from the logging/observability view of headers.
5. No server-side Anthropic key is required: an ``sk-ant-oat`` credential
   resolves to an ``Authorization: Bearer`` header on its own.

It also proves the negative case: an ordinary (non-OAuth) ``Authorization``
bearer that was used to authenticate with LiteLLM is stripped, so a plain proxy
credential can never leak to a provider.

This file never prints a real credential value; the token used here is a
synthetic ``sk-ant-oat`` string that is not a live secret.
"""

from __future__ import annotations

from starlette.datastructures import Headers

from litellm.llms.anthropic.common_utils import (
    AnthropicModelInfo,
    is_anthropic_oauth_key,
)
from litellm.proxy.litellm_pre_call_utils import (
    add_provider_specific_headers_to_request,
    clean_headers,
    redact_credential_headers,
)

# Synthetic, non-live token. Only its ``sk-ant-oat`` prefix is meaningful.
SYNTHETIC_OAUTH_TOKEN = "sk-ant-oat-CONTRACT-TEST-NOT-A-REAL-SECRET"
SYNTHETIC_OAUTH_HEADER = f"Bearer {SYNTHETIC_OAUTH_TOKEN}"
SYNTHETIC_VIRTUAL_KEY_HEADER = "Bearer sk-litellm-virtual-key-not-real"


def _clean_claude_code_headers() -> dict:
    """Run the real proxy header cleaner for a Claude Code subscription call.

    Claude Code authenticates LiteLLM with ``x-litellm-api-key`` while carrying
    the Anthropic OAuth token in ``Authorization``.
    """
    inbound = Headers(
        {
            "x-litellm-api-key": SYNTHETIC_VIRTUAL_KEY_HEADER,
            "authorization": SYNTHETIC_OAUTH_HEADER,
            "content-type": "application/json",
            "anthropic-beta": "oauth-2025-04-20",
        }
    )
    return clean_headers(
        inbound,
        litellm_key_header_name=None,
        forward_llm_provider_auth_headers=False,
        authenticated_with_header="x-litellm-api-key",
    )


def verify_virtual_key_not_forwarded_and_oauth_retained() -> None:
    """Assertions 1 and 2."""
    cleaned = _clean_claude_code_headers()
    cleaned_lower = {k.lower() for k in cleaned}

    # 1. The gateway virtual key must never reach the provider.
    if "x-litellm-api-key" in cleaned_lower:
        raise RuntimeError(
            "x-litellm-api-key must be consumed by gateway auth and never forwarded upstream"
        )

    # 2. The recognized Anthropic OAuth token is retained for Anthropic.
    if cleaned.get("authorization") != SYNTHETIC_OAUTH_HEADER:
        raise RuntimeError(
            "Anthropic subscription OAuth Authorization header was not retained for upstream dispatch"
        )


def verify_oauth_scoped_to_anthropic_only() -> None:
    """Assertion 3."""
    cleaned = _clean_claude_code_headers()
    data: dict = {}
    add_provider_specific_headers_to_request(data=data, headers=cleaned)

    provider_specific = data.get("provider_specific_header")
    if provider_specific is None:
        raise RuntimeError("OAuth Authorization was not scoped to any provider")

    entries = provider_specific if isinstance(provider_specific, list) else [provider_specific]

    def _provider(entry: object) -> str:
        if isinstance(entry, dict):
            return str(entry.get("custom_llm_provider", ""))
        return str(getattr(entry, "custom_llm_provider", ""))

    def _extra_headers(entry: object) -> dict:
        if isinstance(entry, dict):
            return dict(entry.get("extra_headers", {}) or {})
        return dict(getattr(entry, "extra_headers", {}) or {})

    oauth_carrying = [
        entry
        for entry in entries
        if _extra_headers(entry).get("authorization") == SYNTHETIC_OAUTH_HEADER
    ]
    if not oauth_carrying:
        raise RuntimeError("No provider-scoped header carries the OAuth Authorization value")

    # Every scope that carries the OAuth token must be Anthropic; the token
    # must never be attached to a non-Anthropic provider request.
    for entry in oauth_carrying:
        provider = _provider(entry)
        if "anthropic" not in provider.lower():
            raise RuntimeError(
                f"OAuth Authorization leaked to non-Anthropic provider scope {provider!r}"
            )


def verify_oauth_redacted_for_logging() -> None:
    """Assertion 4."""
    cleaned = _clean_claude_code_headers()
    redacted = redact_credential_headers(cleaned)

    if redacted.get("authorization") == SYNTHETIC_OAUTH_HEADER:
        raise RuntimeError("OAuth Authorization value was not redacted for logging")
    if SYNTHETIC_OAUTH_TOKEN in str(redacted):
        raise RuntimeError("OAuth token value is present in the logging-safe header view")


def verify_no_server_side_anthropic_key_required() -> None:
    """Assertion 5."""
    # is_anthropic_oauth_key gates the whole flow: only sk-ant-oat* is treated
    # as a subscription OAuth credential; a normal bearer is not.
    if not is_anthropic_oauth_key(SYNTHETIC_OAUTH_HEADER):
        raise RuntimeError("sk-ant-oat OAuth token was not recognized as an Anthropic OAuth credential")
    if is_anthropic_oauth_key("Bearer sk-not-an-oauth-token"):
        raise RuntimeError("A non-OAuth bearer must not be treated as an Anthropic OAuth credential")

    # An sk-ant-oat credential resolves to an Authorization: Bearer header on
    # its own — no ANTHROPIC_API_KEY / x-api-key needed server-side.
    auth_header = AnthropicModelInfo.get_auth_header(api_key=SYNTHETIC_OAUTH_TOKEN)
    if not auth_header or "authorization" not in {k.lower() for k in auth_header}:
        raise RuntimeError("OAuth credential did not resolve to an Authorization header without a server key")
    if any(k.lower() == "x-api-key" for k in auth_header):
        raise RuntimeError("OAuth credential must not be sent as an x-api-key server credential")


def verify_normal_authorization_used_for_auth_is_stripped() -> None:
    """Negative case: a plain proxy Authorization credential never leaks."""
    inbound = Headers(
        {
            "authorization": "Bearer sk-litellm-normal-virtual-key",
            "content-type": "application/json",
        }
    )
    cleaned = clean_headers(
        inbound,
        litellm_key_header_name=None,
        forward_llm_provider_auth_headers=False,
        authenticated_with_header="authorization",
    )
    if "authorization" in {k.lower() for k in cleaned}:
        raise RuntimeError(
            "A non-OAuth Authorization header used for LiteLLM auth must not be forwarded upstream"
        )


def main() -> None:
    verify_virtual_key_not_forwarded_and_oauth_retained()
    verify_oauth_scoped_to_anthropic_only()
    verify_oauth_redacted_for_logging()
    verify_no_server_side_anthropic_key_required()
    verify_normal_authorization_used_for_auth_is_stripped()
    print(
        "claude-oauth-passthrough-contract: OK "
        "(virtual key not forwarded; sk-ant-oat retained + Anthropic-scoped; "
        "OAuth redacted in logs; no server-side Anthropic key required)"
    )


if __name__ == "__main__":
    main()
