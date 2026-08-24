"""Fail-closed WorkOS Connect authentication for the LiteLLM proxy."""

from __future__ import annotations

import asyncio
import hmac
import hashlib
import json
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final
from urllib.parse import urlsplit

import httpx
import jwt
from fastapi import HTTPException, Request, status
from jwt import PyJWK
from litellm.proxy._types import (
    LiteLLM_ObjectPermissionTable,
    LitellmUserRoles,
    UserAPIKeyAuth,
)

_ALGORITHM: Final = "RS256"
_MAX_TOKEN_BYTES: Final = 16 * 1024
_MAX_JWKS_BYTES: Final = 512 * 1024
_DEFAULT_JWKS_TTL_SECONDS: Final = 300
_DEFAULT_UNKNOWN_KID_REFRESH_SECONDS: Final = 10
_DEFAULT_CLOCK_SKEW_SECONDS: Final = 30
_HTTP_TIMEOUT_SECONDS: Final = 5.0
_WORKOS_POLICY_KEY_ALIAS: Final = "workos-users"
_ALLOWED_DATA_ROUTES: Final = (
    "/chat/completions",
    "/v1/chat/completions",
    "/responses",
    "/v1/responses",
    "/openai/v1/responses",
    "/responses/{response_id}",
    "/v1/responses/{response_id}",
    "/openai/v1/responses/{response_id}",
    "/responses/{response_id}/input_items",
    "/v1/responses/{response_id}/input_items",
    "/openai/v1/responses/{response_id}/input_items",
    "/responses/{response_id}/cancel",
    "/v1/responses/{response_id}/cancel",
    "/openai/v1/responses/{response_id}/cancel",
    "/embeddings",
    "/v1/embeddings",
    "/rerank",
    "/v1/rerank",
    "/v2/rerank",
    "/ocr",
    "/v1/ocr",
    "/images/generations",
    "/v1/images/generations",
    "/images/edits",
    "/v1/images/edits",
    "/models",
    "/v1/models",
    "/rag/ingest",
    "/v1/rag/ingest",
    "/rag/query",
    "/v1/rag/query",
    "mcp_inference_routes",
)


class WorkOSConfigurationError(RuntimeError):
    """Raised when runtime WorkOS configuration is unsafe or incomplete."""


class WorkOSAuthenticationError(Exception):
    """Internal denial carrying only a bounded, non-sensitive reason code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value or value == "*":
        raise WorkOSConfigurationError(f"{name} must be configured explicitly")
    return value


def _bounded_int(
    env: Mapping[str, str],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = env.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise WorkOSConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise WorkOSConfigurationError(
            f"{name} must be between {minimum} and {maximum} seconds"
        )
    return value


def _csv(env: Mapping[str, str], name: str, *, required: bool) -> tuple[str, ...]:
    values = tuple(dict.fromkeys(part.strip() for part in env.get(name, "").split(",") if part.strip()))
    if required and not values:
        raise WorkOSConfigurationError(f"{name} must contain at least one value")
    return values


def _tool_permissions(env: Mapping[str, str]) -> dict[str, list[str]]:
    raw = _required(env, "WORKOS_MCP_TOOL_PERMISSIONS_JSON")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WorkOSConfigurationError("WORKOS_MCP_TOOL_PERMISSIONS_JSON must be valid JSON") from exc
    if not isinstance(payload, dict) or not payload:
        raise WorkOSConfigurationError(
            "WORKOS_MCP_TOOL_PERMISSIONS_JSON must be a non-empty object"
        )
    parsed: dict[str, list[str]] = {}
    for server, tools in payload.items():
        if (
            not isinstance(server, str)
            or not server.strip()
            or not isinstance(tools, list)
            or not tools
            or any(not isinstance(tool, str) or not tool.strip() for tool in tools)
        ):
            raise WorkOSConfigurationError(
                "WORKOS_MCP_TOOL_PERMISSIONS_JSON must map server names to non-empty tool-name lists"
            )
        parsed[server] = list(dict.fromkeys(tool.strip() for tool in tools))
    return parsed


def _https_url(name: str, value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise WorkOSConfigurationError(f"{name} must be an HTTPS URL without credentials or a fragment")
    return value


@dataclass(frozen=True, slots=True)
class WorkOSSettings:
    """Trusted resource-server and common-permission configuration."""

    issuer: str
    jwks_url: str
    audience: str
    org_id: str
    allowed_models: tuple[str, ...]
    mcp_servers: tuple[str, ...]
    mcp_access_groups: tuple[str, ...]
    mcp_tool_permissions: dict[str, list[str]]
    jwks_cache_ttl_seconds: int = _DEFAULT_JWKS_TTL_SECONDS
    unknown_kid_refresh_seconds: int = _DEFAULT_UNKNOWN_KID_REFRESH_SECONDS
    clock_skew_seconds: int = _DEFAULT_CLOCK_SKEW_SECONDS

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> "WorkOSSettings":
        values = os.environ if env is None else env
        issuer = _https_url("WORKOS_ISSUER", _required(values, "WORKOS_ISSUER"))
        jwks_url = _https_url("WORKOS_JWKS_URL", _required(values, "WORKOS_JWKS_URL"))
        audience = _https_url("WORKOS_AUDIENCE", _required(values, "WORKOS_AUDIENCE"))

        issuer_url = urlsplit(issuer)
        jwks = urlsplit(jwks_url)
        if (issuer_url.scheme, issuer_url.hostname, issuer_url.port) != (
            jwks.scheme,
            jwks.hostname,
            jwks.port,
        ):
            raise WorkOSConfigurationError("WORKOS_JWKS_URL must use the WORKOS_ISSUER origin")
        if jwks.path != "/oauth2/jwks" or jwks.query:
            raise WorkOSConfigurationError("WORKOS_JWKS_URL must use the WorkOS /oauth2/jwks endpoint")

        mcp_servers = _csv(values, "WORKOS_MCP_SERVERS", required=False)
        mcp_access_groups = _csv(values, "WORKOS_MCP_ACCESS_GROUPS", required=False)
        if not mcp_servers and not mcp_access_groups:
            raise WorkOSConfigurationError(
                "at least one WORKOS_MCP_SERVERS or WORKOS_MCP_ACCESS_GROUPS value is required"
            )

        return cls(
            issuer=issuer,
            jwks_url=jwks_url,
            audience=audience,
            org_id=_required(values, "WORKOS_ORG_ID"),
            allowed_models=_csv(values, "WORKOS_ALLOWED_MODELS", required=True),
            mcp_servers=mcp_servers,
            mcp_access_groups=mcp_access_groups,
            mcp_tool_permissions=_tool_permissions(values),
            jwks_cache_ttl_seconds=_bounded_int(
                values, "WORKOS_JWKS_CACHE_TTL_SECONDS", _DEFAULT_JWKS_TTL_SECONDS, 30, 3600
            ),
            unknown_kid_refresh_seconds=_bounded_int(
                values,
                "WORKOS_UNKNOWN_KID_REFRESH_SECONDS",
                _DEFAULT_UNKNOWN_KID_REFRESH_SECONDS,
                1,
                60,
            ),
            clock_skew_seconds=_bounded_int(
                values, "WORKOS_CLOCK_SKEW_SECONDS", _DEFAULT_CLOCK_SKEW_SECONDS, 0, 120
            ),
        )


JWKSFetcher = Callable[[str], Awaitable[dict[str, Any]]]


async def _fetch_jwks(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(_HTTP_TIMEOUT_SECONDS),
    ) as client:
        response = await client.get(url, headers={"Accept": "application/json"})
        response.raise_for_status()
        if len(response.content) > _MAX_JWKS_BYTES:
            raise WorkOSAuthenticationError("jwks_response_too_large")
        payload = response.json()
    if not isinstance(payload, dict):
        raise WorkOSAuthenticationError("invalid_jwks")
    return payload


class WorkOSJWKSCache:
    """Bounded JWKS cache with key-rotation refresh and unknown-kid throttling."""

    def __init__(
        self,
        settings: WorkOSSettings,
        *,
        fetcher: JWKSFetcher = _fetch_jwks,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._fetcher = fetcher
        self._monotonic = monotonic
        self._keys: dict[str, PyJWK] = {}
        self._expires_at = 0.0
        self._last_refresh_at = float("-inf")
        self._lock = asyncio.Lock()

    @staticmethod
    def _parse_keys(payload: dict[str, Any]) -> dict[str, PyJWK]:
        raw_keys = payload.get("keys")
        if not isinstance(raw_keys, list) or not 1 <= len(raw_keys) <= 100:
            raise WorkOSAuthenticationError("invalid_jwks")

        parsed: dict[str, PyJWK] = {}
        for raw_key in raw_keys:
            if not isinstance(raw_key, dict):
                continue
            kid = raw_key.get("kid")
            if (
                not isinstance(kid, str)
                or not kid
                or raw_key.get("kty") != "RSA"
                or raw_key.get("use") not in (None, "sig")
                or raw_key.get("alg") not in (None, _ALGORITHM)
            ):
                continue
            try:
                key = PyJWK.from_dict(raw_key, algorithm=_ALGORITHM)
            except Exception:
                continue
            parsed[kid] = key

        if not parsed:
            raise WorkOSAuthenticationError("invalid_jwks")
        return parsed

    async def _refresh(self, now: float) -> None:
        try:
            payload = await self._fetcher(self._settings.jwks_url)
            keys = self._parse_keys(payload)
        except WorkOSAuthenticationError:
            raise
        except Exception as exc:
            raise WorkOSAuthenticationError("jwks_unavailable") from exc
        self._keys = keys
        self._last_refresh_at = now
        self._expires_at = now + self._settings.jwks_cache_ttl_seconds

    async def get(self, kid: str) -> PyJWK:
        now = self._monotonic()
        key = self._keys.get(kid)
        if key is not None and now < self._expires_at:
            return key

        async with self._lock:
            now = self._monotonic()
            key = self._keys.get(kid)
            cache_expired = now >= self._expires_at
            unknown_refresh_allowed = (
                key is None
                and now - self._last_refresh_at >= self._settings.unknown_kid_refresh_seconds
            )
            if cache_expired or unknown_refresh_allowed:
                await self._refresh(now)
                key = self._keys.get(kid)
            if key is None:
                raise WorkOSAuthenticationError("unknown_signing_key")
            if now >= self._expires_at:
                raise WorkOSAuthenticationError("jwks_expired")
            return key


class WorkOSCustomAuth:
    """Callable implementing LiteLLM's open-source ``custom_auth`` contract."""

    is_workos_connect_auth: Final = True

    def __init__(
        self,
        settings: WorkOSSettings,
        *,
        jwks_cache: WorkOSJWKSCache | None = None,
    ) -> None:
        self.settings = settings
        self.jwks_cache = jwks_cache or WorkOSJWKSCache(settings)

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> "WorkOSCustomAuth":
        return cls(WorkOSSettings.from_environment(env))

    @staticmethod
    def _request_token(request: Request, api_key: str) -> str:
        if not isinstance(api_key, str):
            raise WorkOSAuthenticationError("missing_bearer")

        raw_headers = request.scope.get("headers", [])
        authorization_values: list[str] = []
        forbidden_credential_headers = {b"x-litellm-api-key", b"x-api-key", b"api-key"}
        for raw_name, raw_value in raw_headers:
            name = raw_name.lower()
            if name in forbidden_credential_headers:
                raise WorkOSAuthenticationError("conflicting_credential")
            if name == b"authorization":
                try:
                    authorization_values.append(raw_value.decode("latin-1"))
                except UnicodeDecodeError as exc:
                    raise WorkOSAuthenticationError("invalid_bearer") from exc

        if len(authorization_values) != 1:
            raise WorkOSAuthenticationError("single_bearer_required")
        authorization = authorization_values[0]
        if not authorization.startswith("Bearer "):
            raise WorkOSAuthenticationError("invalid_bearer")
        value = authorization[7:]
        if not value or value != value.strip() or any(char.isspace() for char in value):
            raise WorkOSAuthenticationError("invalid_bearer")
        if len(value.encode("utf-8")) > _MAX_TOKEN_BYTES:
            raise WorkOSAuthenticationError("invalid_bearer")
        passed_value = api_key.strip()
        if passed_value.lower().startswith("bearer "):
            passed_value = passed_value[7:].strip()
        if not hmac.compare_digest(value, passed_value):
            raise WorkOSAuthenticationError("credential_mismatch")
        return value

    async def _validate(self, token: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise WorkOSAuthenticationError("malformed_jwt") from exc

        if header.get("alg") != _ALGORITHM:
            raise WorkOSAuthenticationError("algorithm_not_allowed")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise WorkOSAuthenticationError("signing_key_required")

        signing_key = await self.jwks_cache.get(kid)
        try:
            claims = jwt.decode(
                token,
                key=signing_key.key,
                algorithms=[_ALGORITHM],
                audience=self.settings.audience,
                issuer=self.settings.issuer,
                leeway=self.settings.clock_skew_seconds,
                options={
                    "require": ["iss", "aud", "sub", "exp", "iat"],
                    "strict_aud": True,
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )
        except jwt.PyJWTError as exc:
            raise WorkOSAuthenticationError("invalid_token") from exc

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise WorkOSAuthenticationError("subject_required")
        for time_claim in ("exp", "iat"):
            value = claims.get(time_claim)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise WorkOSAuthenticationError("invalid_time_claim")
        nbf = claims.get("nbf")
        if nbf is not None and (isinstance(nbf, bool) or not isinstance(nbf, (int, float))):
            raise WorkOSAuthenticationError("invalid_time_claim")
        if claims.get("org_id") != self.settings.org_id:
            raise WorkOSAuthenticationError("organization_not_allowed")
        return claims

    def _identity(self, token: str, claims: dict[str, Any]) -> UserAPIKeyAuth:
        subject = claims["sub"]
        del token
        principal_material = "\0".join(
            (self.settings.issuer, self.settings.org_id, subject)
        ).encode("utf-8")
        principal_hash = hashlib.sha256(principal_material).hexdigest()
        permission_id = hashlib.sha256(self.settings.org_id.encode("utf-8")).hexdigest()[:16]
        object_permission = LiteLLM_ObjectPermissionTable(
            object_permission_id=f"workos-{permission_id}",
            models=list(self.settings.allowed_models),
            mcp_servers=list(self.settings.mcp_servers),
            mcp_access_groups=list(self.settings.mcp_access_groups),
            mcp_tool_permissions=self.settings.mcp_tool_permissions,
        )
        auth = UserAPIKeyAuth(
            token=f"workos-principal-{principal_hash}",
            key_alias=_WORKOS_POLICY_KEY_ALIAS,
            user_id=subject,
            end_user_id=subject,
            org_id=self.settings.org_id,
            user_role=LitellmUserRoles.INTERNAL_USER,
            models=list(self.settings.allowed_models),
            allowed_routes=list(_ALLOWED_DATA_ROUTES),
            expires=datetime.fromtimestamp(claims["exp"], tz=timezone.utc),
            object_permission=object_permission,
            metadata={
                "auth_provider": "workos_connect",
                "auth_issuer": self.settings.issuer,
                "workos_org_id": self.settings.org_id,
            },
        )
        # Added to the pinned LiteLLM type by the image compatibility patch.
        # This server-only bit scrubs the admission bearer before MCP egress
        # without granting the separate DCR-session permission semantics.
        auth.workos_admitted_subject = True  # type: ignore[attr-defined]
        return auth

    async def __call__(self, request: Request, api_key: str) -> UserAPIKeyAuth:
        try:
            token = self._request_token(request, api_key)
            claims = await self._validate(token)
            return self._identity(token, claims)
        except WorkOSAuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid_token",
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            ) from exc


def configuration_summary(settings: WorkOSSettings) -> str:
    """Return a secret-safe startup summary suitable for validation output."""

    return json.dumps(
        {
            "issuer_configured": bool(settings.issuer),
            "jwks_configured": bool(settings.jwks_url),
            "audience_configured": bool(settings.audience),
            "organization_configured": bool(settings.org_id),
            "allowed_model_count": len(settings.allowed_models),
            "mcp_server_count": len(settings.mcp_servers),
            "mcp_access_group_count": len(settings.mcp_access_groups),
            "mcp_tool_permission_server_count": len(settings.mcp_tool_permissions),
        },
        sort_keys=True,
    )
