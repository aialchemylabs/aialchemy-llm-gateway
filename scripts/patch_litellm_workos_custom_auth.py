#!/usr/bin/env python3
"""Integrate fail-closed WorkOS custom auth with pinned LiteLLM 1.97.0.

The upstream custom-auth hook is open source, but three pinned-version gaps
matter for this deployment:

* common authorization checks treat ``master_key=None`` as no-auth mode even
  when custom auth is configured;
* Responses WebSocket repeats the same no-auth shortcut;
* MCP can bypass gateway admission for delegated/passthrough targets and can
  forward a validated bearer to an upstream MCP server.

This patch makes configured WorkOS auth an authentication mode, routes every
non-metadata MCP request through it, and adds a server-only marker used solely
to scrub the admission bearer before MCP egress.
"""

from __future__ import annotations

import argparse
from importlib.util import find_spec
from pathlib import Path
from typing import Final


TYPE_MARKER_OLD: Final = """    budget_reservation: dict[str, Any] | None = Field(default=None, exclude=True)
"""
TYPE_MARKER_NEW: Final = """    # Set only after WorkOS custom-auth validation. This marker is separate
    # from mcp_admitted_user_subject because WorkOS users must not inherit the
    # DCR-session team's permission-union semantics. It exists only to scrub
    # the resource-server bearer before MCP egress.
    workos_admitted_subject: bool = Field(default=False, exclude=True)
    budget_reservation: dict[str, Any] | None = Field(default=None, exclude=True)
"""

TYPE_VALIDATOR_OLD: Final = """        values.pop("via_virtual_key", None)
"""
TYPE_VALIDATOR_NEW: Final = """        values.pop("via_virtual_key", None)
        values.pop("workos_admitted_subject", None)
"""

PUBLIC_ROUTES_OLD: Final = '''    public_routes = frozenset(
        (
            "/routes",
            "/",
            "/health/liveliness",
            "/health/liveness",
            "/test",
            "/config/yaml",
            "/litellm/.well-known/litellm-ui-config",
            "/.well-known/litellm-ui-config",
            "/public/model_hub",
            "/public/model_hub/info",
            "/public/agent_hub",
            "/public/mcp_hub",
            "/public/skill_hub",
            "/public/litellm_model_cost_map",
        )
    )
'''
PUBLIC_ROUTES_NEW: Final = '''    # This resource server exposes only liveness probes without auth.
    # OAuth protected-resource metadata is handled by the MCP well-known
    # routes; catalogues, config, route inventory, and UI discovery are not
    # public in the WorkOS deployment.
    public_routes = frozenset(("/health/liveliness", "/health/liveness"))
'''

COMMON_CHECK_OLD: Final = """    if master_key is None and not (
        general_settings.get("enable_jwt_auth", False)
        or general_settings.get("enable_oauth2_auth", False)
        or general_settings.get("enable_oauth2_proxy_auth", False)
    ):
        return
"""
COMMON_CHECK_NEW: Final = """    if master_key is None and user_custom_auth is None and not (
        general_settings.get("enable_jwt_auth", False)
        or general_settings.get("enable_oauth2_auth", False)
        or general_settings.get("enable_oauth2_proxy_auth", False)
    ):
        return
"""

RESPONSES_CHECK_OLD: Final = """    if master_key is None and not (
        general_settings.get("enable_jwt_auth", False)
        or general_settings.get("enable_oauth2_auth", False)
        or general_settings.get("enable_oauth2_proxy_auth", False)
    ):
        return
"""
RESPONSES_CHECK_NEW: Final = """    if master_key is None and user_custom_auth is None and not (
        general_settings.get("enable_jwt_auth", False)
        or general_settings.get("enable_oauth2_auth", False)
        or general_settings.get("enable_oauth2_proxy_auth", False)
    ):
        return
"""

MCP_ROUTE_OLD: Final = """        request_route: Final = get_request_route(request)
        # Only OAuth metadata routes registered under /.well-known/ are public.
"""
MCP_ROUTE_NEW: Final = """        request_route: Final = get_request_route(request)
        # WorkOS is the resource-server admission credential for every MCP
        # target. Do not let a per-server delegated/passthrough setting bypass
        # gateway authentication.
        from litellm.proxy.proxy_server import user_custom_auth as configured_custom_auth  # noqa: PLC0415

        workos_custom_auth_enabled: Final = (
            getattr(configured_custom_auth, "is_workos_connect_auth", False) is True
        )
        # Only OAuth metadata routes registered under /.well-known/ are public.
"""

MCP_BRANCH_OLD: Final = """        if request_route.startswith("/.well-known/"):
            validated_user_api_key_auth = UserAPIKeyAuth()
        elif has_explicit_litellm_key:
"""
MCP_BRANCH_NEW: Final = """        if request_route.startswith("/.well-known/"):
            validated_user_api_key_auth = UserAPIKeyAuth()
        elif workos_custom_auth_enabled:
            # Covers missing credentials too: custom auth emits the canonical
            # 401 and no delegated/true-passthrough branch can admit anonymously.
            try:
                validated_user_api_key_auth = await user_api_key_auth(
                    api_key=litellm_api_key,
                    request=request,
                )
            except Exception as exc:
                if _is_litellm_auth_admission_error(exc):
                    raise _workos_resource_challenge(
                        request,
                        invalid_token=bool(litellm_api_key),
                    ) from exc
                raise
        elif has_explicit_litellm_key:
"""

MCP_SCRUB_OLD: Final = """            admitted=_is_mcp_admitted_user_subject(validated_user_api_key_auth),
"""
MCP_SCRUB_NEW: Final = """            admitted=(
                _is_mcp_admitted_user_subject(validated_user_api_key_auth)
                or getattr(validated_user_api_key_auth, "workos_admitted_subject", False) is True
            ),
"""

MCP_CHALLENGE_OLD: Final = """def _has_client_supplied_mcp_auth(
"""
MCP_CHALLENGE_NEW: Final = '''def _workos_resource_challenge(request: Request, *, invalid_token: bool) -> HTTPException:
    """Challenge against the canonical WorkOS protected-resource document."""
    resource_metadata_url: Final = (
        f"{get_request_base_url(request)}/.well-known/oauth-protected-resource"
        f"{well_known_root_suffix()}/mcp"
    )
    error_attr: Final = 'error="invalid_token", ' if invalid_token else ""
    return HTTPException(
        status_code=401,
        detail={
            "error": "authentication_required",
            "message": "Authenticate with WorkOS to use the MCP endpoint.",
        },
        headers={"WWW-Authenticate": f'Bearer {error_attr}resource_metadata="{resource_metadata_url}"'},
    )


def _has_client_supplied_mcp_auth(
'''

DISCOVERY_IMPORT_OLD: Final = """import asyncio
import html as _html
"""
DISCOVERY_IMPORT_NEW: Final = """import asyncio
import html as _html
import os
"""

DISCOVERY_HELPER_OLD: Final = """async def _build_oauth_protected_resource_response(
"""
DISCOVERY_HELPER_NEW: Final = '''def _workos_protected_resource_metadata() -> dict | None:
    issuer: Final = os.getenv("WORKOS_ISSUER", "").strip()
    resource: Final = os.getenv("WORKOS_AUDIENCE", "").strip()
    if not issuer or not resource:
        return None
    return {
        "resource": resource,
        "authorization_servers": [issuer],
        "bearer_methods_supported": ["header"],
    }


async def _build_oauth_protected_resource_response(
'''

DISCOVERY_NAMED_OLD: Final = '''        OAuth protected resource metadata dict
    """
    from litellm.proxy._experimental.mcp_server.mcp_server_manager import (
        global_mcp_server_manager,
    )

    request_base_url: Final = get_request_base_url(request)
'''
DISCOVERY_NAMED_NEW: Final = '''        OAuth protected resource metadata dict
    """
    workos_metadata: Final = _workos_protected_resource_metadata()
    if workos_metadata is not None:
        return workos_metadata

    from litellm.proxy._experimental.mcp_server.mcp_server_manager import (
        global_mcp_server_manager,
    )

    request_base_url: Final = get_request_base_url(request)
'''

DISCOVERY_AGGREGATE_OLD: Final = '''    request_base_url: Final = get_request_base_url(request)
    return {
        "authorization_servers": [f"{request_base_url}/mcp"],
        "resource": f"{request_base_url}/mcp",
        "scopes_supported": [],
    }
'''
DISCOVERY_AGGREGATE_NEW: Final = '''    workos_metadata: Final = _workos_protected_resource_metadata()
    if workos_metadata is not None:
        return workos_metadata

    request_base_url: Final = get_request_base_url(request)
    return {
        "authorization_servers": [f"{request_base_url}/mcp"],
        "resource": f"{request_base_url}/mcp",
        "scopes_supported": [],
    }
'''


def installed_proxy_root() -> Path:
    spec = find_spec("litellm")
    if spec is None or spec.origin is None:
        raise RuntimeError("The installed litellm package could not be located")
    return Path(spec.origin).parent / "proxy"


def _replace_once(path: Path, old: str, new: str, label: str) -> str:
    source = path.read_text(encoding="utf-8")
    if source.count(new) == 1:
        return "already-patched"
    matches = source.count(old)
    if matches != 1:
        raise RuntimeError(f"Expected one {label} block in {path}; found {matches}")
    path.write_text(source.replace(old, new), encoding="utf-8")
    return "patched"


def patch_tree(proxy_root: Path) -> str:
    changes = (
        (
            proxy_root / "_types.py",
            TYPE_MARKER_OLD,
            TYPE_MARKER_NEW,
            "WorkOS server marker",
        ),
        (
            proxy_root / "_types.py",
            TYPE_VALIDATOR_OLD,
            TYPE_VALIDATOR_NEW,
            "WorkOS marker input scrub",
        ),
        (
            proxy_root / "_types.py",
            PUBLIC_ROUTES_OLD,
            PUBLIC_ROUTES_NEW,
            "WorkOS public-route allowlist",
        ),
        (
            proxy_root / "auth" / "user_api_key_auth.py",
            COMMON_CHECK_OLD,
            COMMON_CHECK_NEW,
            "custom-auth common-check gate",
        ),
        (
            proxy_root / "response_api_endpoints" / "endpoints.py",
            RESPONSES_CHECK_OLD,
            RESPONSES_CHECK_NEW,
            "Responses WebSocket custom-auth gate",
        ),
        (
            proxy_root / "_experimental" / "mcp_server" / "auth" / "user_api_key_auth_mcp.py",
            MCP_ROUTE_OLD,
            MCP_ROUTE_NEW,
            "MCP WorkOS mode detection",
        ),
        (
            proxy_root / "_experimental" / "mcp_server" / "auth" / "user_api_key_auth_mcp.py",
            MCP_BRANCH_OLD,
            MCP_BRANCH_NEW,
            "MCP WorkOS admission",
        ),
        (
            proxy_root / "_experimental" / "mcp_server" / "auth" / "user_api_key_auth_mcp.py",
            MCP_SCRUB_OLD,
            MCP_SCRUB_NEW,
            "MCP WorkOS bearer scrub",
        ),
        (
            proxy_root / "_experimental" / "mcp_server" / "auth" / "user_api_key_auth_mcp.py",
            MCP_CHALLENGE_OLD,
            MCP_CHALLENGE_NEW,
            "MCP WorkOS challenge",
        ),
        (
            proxy_root / "_experimental" / "mcp_server" / "discoverable_endpoints.py",
            DISCOVERY_IMPORT_OLD,
            DISCOVERY_IMPORT_NEW,
            "MCP WorkOS discovery import",
        ),
        (
            proxy_root / "_experimental" / "mcp_server" / "discoverable_endpoints.py",
            DISCOVERY_HELPER_OLD,
            DISCOVERY_HELPER_NEW,
            "MCP WorkOS discovery helper",
        ),
        (
            proxy_root / "_experimental" / "mcp_server" / "discoverable_endpoints.py",
            DISCOVERY_NAMED_OLD,
            DISCOVERY_NAMED_NEW,
            "MCP named WorkOS metadata",
        ),
        (
            proxy_root / "_experimental" / "mcp_server" / "discoverable_endpoints.py",
            DISCOVERY_AGGREGATE_OLD,
            DISCOVERY_AGGREGATE_NEW,
            "MCP aggregate WorkOS metadata",
        ),
    )
    results = [_replace_once(path, old, new, label) for path, old, new, label in changes]
    return "already-patched" if all(result == "already-patched" for result in results) else "patched"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("proxy_root", nargs="?", type=Path)
    args = parser.parse_args()
    root = args.proxy_root if args.proxy_root is not None else installed_proxy_root()
    result = patch_tree(root)
    print(f"{result}: {root}")


if __name__ == "__main__":
    main()
