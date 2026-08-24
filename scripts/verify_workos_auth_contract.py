#!/usr/bin/env python3
"""Verify the WorkOS contract against the installed, patched LiteLLM image."""

from __future__ import annotations

import os
from importlib.util import find_spec
from pathlib import Path
from time import time

from aialchemy_auth.workos import WorkOSCustomAuth, WorkOSSettings
from litellm.proxy._types import LiteLLMRoutes, UserAPIKeyAuth
from litellm.proxy.types_utils.utils import get_instance_fn


def _litellm_proxy_root() -> Path:
    spec = find_spec("litellm")
    if spec is None or spec.origin is None:
        raise RuntimeError("installed LiteLLM package not found")
    return Path(spec.origin).parent / "proxy"


def main() -> None:
    marker = UserAPIKeyAuth.model_fields.get("workos_admitted_subject")
    if marker is None or marker.exclude is not True:
        raise RuntimeError("server-only WorkOS admission marker is not active")
    if LiteLLMRoutes.public_routes.value != frozenset(
        ("/health/liveliness", "/health/liveness")
    ):
        raise RuntimeError("unexpected unauthenticated LiteLLM routes remain public")

    root = _litellm_proxy_root()
    common_auth = (root / "auth" / "user_api_key_auth.py").read_text(encoding="utf-8")
    responses = (root / "response_api_endpoints" / "endpoints.py").read_text(encoding="utf-8")
    mcp_auth = (
        root / "_experimental" / "mcp_server" / "auth" / "user_api_key_auth_mcp.py"
    ).read_text(encoding="utf-8")
    mcp_discovery = (
        root / "_experimental" / "mcp_server" / "discoverable_endpoints.py"
    ).read_text(encoding="utf-8")
    expected_common_gate = "master_key is None and user_custom_auth is None"
    if expected_common_gate not in common_auth or expected_common_gate not in responses:
        raise RuntimeError("custom-auth common authorization gate is not active")
    if "elif workos_custom_auth_enabled:" not in mcp_auth:
        raise RuntimeError("MCP WorkOS admission gate is not active")
    if 'getattr(validated_user_api_key_auth, "workos_admitted_subject", False)' not in mcp_auth:
        raise RuntimeError("MCP WorkOS bearer scrub is not active")
    if "def _workos_resource_challenge" not in mcp_auth:
        raise RuntimeError("MCP WorkOS RFC 9728 challenge is not active")
    if "def _workos_protected_resource_metadata" not in mcp_discovery:
        raise RuntimeError("MCP WorkOS protected-resource metadata is not active")

    settings = WorkOSSettings(
        issuer="https://example.authkit.app",
        jwks_url="https://example.authkit.app/oauth2/jwks",
        audience="https://gateway.example/mcp",
        org_id="org_example",
        allowed_models=("model-a",),
        mcp_servers=("server-a",),
        mcp_access_groups=(),
        mcp_tool_permissions={"server-a": ["safe_tool"]},
    )
    identity = WorkOSCustomAuth(settings)._identity(
        "verification-token",
        {
            "sub": "user_example",
            "org_id": "org_example",
            "exp": int(time()) + 300,
        },
    )
    if identity.workos_admitted_subject is not True:
        raise RuntimeError("WorkOS identity is not marked for MCP bearer scrubbing")
    if identity.key_alias != "workos-users":
        raise RuntimeError("WorkOS identity is not attached to the common guardrail policy alias")
    if not identity.allowed_routes or not identity.models:
        raise RuntimeError("WorkOS identity has an unrestricted empty route/model allowlist")
    if identity.object_permission is None or not identity.object_permission.mcp_tool_permissions:
        raise RuntimeError("WorkOS identity lacks explicit MCP tool permissions")
    if "verification-token" in str(identity.model_dump()):
        raise RuntimeError("raw WorkOS token escaped into the LiteLLM identity")

    os.environ.update(
        {
            "WORKOS_ISSUER": settings.issuer,
            "WORKOS_JWKS_URL": settings.jwks_url,
            "WORKOS_AUDIENCE": settings.audience,
            "WORKOS_ORG_ID": settings.org_id,
            "WORKOS_ALLOWED_MODELS": "model-a",
            "WORKOS_MCP_SERVERS": "server-a",
            "WORKOS_MCP_TOOL_PERMISSIONS_JSON": '{"server-a":["safe_tool"]}',
        }
    )
    loaded = get_instance_fn("aialchemy_auth.runtime.workos_auth")
    if not isinstance(loaded, WorkOSCustomAuth) or loaded.is_workos_connect_auth is not True:
        raise RuntimeError("LiteLLM custom_auth runtime binding is invalid")

    print("workos-auth: installed LiteLLM contract verified")


if __name__ == "__main__":
    main()
