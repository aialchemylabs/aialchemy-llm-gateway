from __future__ import annotations

import unittest
from collections.abc import Mapping
from time import time
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException, Request
from jwt.algorithms import RSAAlgorithm

from aialchemy_auth.workos import (
    WorkOSAuthenticationError,
    WorkOSConfigurationError,
    WorkOSCustomAuth,
    WorkOSJWKSCache,
    WorkOSSettings,
)


SUPPORTED_WORKOS_MODEL_CATALOG = (
    "gemini/gemini-3.5-flash",
    "gemini/gemini-3.5-flash-lite",
    "gemini/gemini-3.6-flash",
    "gemini/gemini-embedding-2",
    "gemini/gemini-3.1-flash-image",
    "chatgpt/gpt-5.6-sol",
    "chatgpt/gpt-5.6-terra",
    "chatgpt/gpt-5.6-luna",
    "cohere/rerank-v4.0-fast",
    "qwen/qwen3-embedding-8b",
    "mistral/mistral-ocr-latest",
    "mistral/mistral-ocr-4",
)


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class WorkOSAuthTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self) -> None:
        self.settings = WorkOSSettings(
            issuer="https://aialchemy.authkit.app",
            jwks_url="https://aialchemy.authkit.app/oauth2/jwks",
            audience="https://llm.aialchemy.au/mcp",
            org_id="org_aialchemy",
            allowed_models=("model-a", "model-b"),
            mcp_servers=("microsoft365",),
            mcp_access_groups=("aialchemy_mcp",),
            mcp_tool_permissions={"microsoft365": ["search_mail", "list_calendar"]},
        )
        self.clock = _Clock()
        self.fetch_count = 0

    @staticmethod
    def _jwk(private_key, kid: str) -> dict[str, Any]:
        jwk = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
        jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
        return jwk

    async def _fetch_primary(self, url: str) -> dict[str, Any]:
        self.assertEqual(url, self.settings.jwks_url)
        self.fetch_count += 1
        return {"keys": [self._jwk(self.private_key, "key-1")]}

    def _claims(self, **updates: Any) -> dict[str, Any]:
        now = int(time())
        claims: dict[str, Any] = {
            "iss": self.settings.issuer,
            "aud": self.settings.audience,
            "sub": "user_123",
            "org_id": self.settings.org_id,
            "iat": now - 5,
            "exp": now + 300,
        }
        claims.update(updates)
        return claims

    def _token(
        self,
        claims: Mapping[str, Any] | None = None,
        *,
        key=None,
        kid: str = "key-1",
        algorithm: str = "RS256",
    ) -> str:
        signing_key = self.private_key if key is None else key
        return jwt.encode(
            dict(self._claims() if claims is None else claims),
            signing_key,
            algorithm=algorithm,
            headers={"kid": kid},
        )

    @staticmethod
    def _request(token: str, *extra_headers: tuple[bytes, bytes]) -> Request:
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1/chat/completions",
                "headers": [(b"authorization", f"Bearer {token}".encode()), *extra_headers],
            }
        )

    def _auth(self) -> WorkOSCustomAuth:
        cache = WorkOSJWKSCache(
            self.settings,
            fetcher=self._fetch_primary,
            monotonic=self.clock,
        )
        return WorkOSCustomAuth(self.settings, jwks_cache=cache)

    async def test_valid_token_maps_to_explicit_common_permissions(self) -> None:
        token = self._token()
        identity = await self._auth()(self._request(token), token)

        self.assertEqual(identity.user_id, "user_123")
        self.assertEqual(identity.end_user_id, "user_123")
        self.assertEqual(identity.org_id, self.settings.org_id)
        self.assertEqual(identity.key_alias, "workos-users")
        self.assertEqual(identity.models, ["model-a", "model-b"])
        self.assertTrue(identity.allowed_routes)
        self.assertNotIn("management_routes", identity.allowed_routes)
        self.assertTrue(identity.workos_admitted_subject)
        self.assertIsNotNone(identity.object_permission)
        self.assertEqual(identity.object_permission.mcp_servers, ["microsoft365"])
        self.assertEqual(
            identity.object_permission.mcp_tool_permissions,
            {"microsoft365": ["search_mail", "list_calendar"]},
        )
        self.assertNotIn(token, str(identity.model_dump()))

    async def test_client_claims_do_not_change_permissions(self) -> None:
        jarvis_token = self._token(self._claims(client_id="jarvis", scope="ignored"))
        copilot_token = self._token(self._claims(client_id="copilot", scope="different"))
        jarvis = await self._auth()(self._request(jarvis_token), jarvis_token)
        copilot = await self._auth()(self._request(copilot_token), copilot_token)

        self.assertEqual(jarvis.models, copilot.models)
        self.assertEqual(jarvis.allowed_routes, copilot.allowed_routes)
        self.assertEqual(jarvis.object_permission, copilot.object_permission)

    async def test_full_supported_catalog_is_preserved_generically(self) -> None:
        env = {
            "WORKOS_ISSUER": self.settings.issuer,
            "WORKOS_JWKS_URL": self.settings.jwks_url,
            "WORKOS_AUDIENCE": self.settings.audience,
            "WORKOS_ORG_ID": self.settings.org_id,
            "WORKOS_ALLOWED_MODELS": ",".join(SUPPORTED_WORKOS_MODEL_CATALOG),
            "WORKOS_MCP_SERVERS": "microsoft365",
            "WORKOS_MCP_TOOL_PERMISSIONS_JSON": '{"microsoft365":["search_mail"]}',
        }
        settings = WorkOSSettings.from_environment(env)
        cache = WorkOSJWKSCache(
            settings,
            fetcher=self._fetch_primary,
            monotonic=self.clock,
        )
        auth = WorkOSCustomAuth(settings, jwks_cache=cache)
        token = self._token(self._claims(client_id="ignored", scope="ignored"))

        identity = await auth(self._request(token), token)

        self.assertEqual(settings.allowed_models, SUPPORTED_WORKOS_MODEL_CATALOG)
        self.assertEqual(identity.models, list(SUPPORTED_WORKOS_MODEL_CATALOG))
        self.assertEqual(len(identity.models), 12)

    async def test_ocr_routes_are_explicit_without_broad_route_access(self) -> None:
        token = self._token()
        identity = await self._auth()(self._request(token), token)

        self.assertIn("/ocr", identity.allowed_routes)
        self.assertIn("/v1/ocr", identity.allowed_routes)
        self.assertNotIn("/ocr/{path:path}", identity.allowed_routes)
        self.assertNotIn("/v1/ocr/{path:path}", identity.allowed_routes)
        self.assertNotIn("management_routes", identity.allowed_routes)

    async def test_identity_fingerprint_is_stable_across_token_refresh(self) -> None:
        first_token = self._token(self._claims(iat=int(time()) - 10, exp=int(time()) + 100))
        second_token = self._token(self._claims(iat=int(time()) - 5, exp=int(time()) + 300))
        first = await self._auth()(self._request(first_token), first_token)
        second = await self._auth()(self._request(second_token), second_token)
        self.assertEqual(first.token, second.token)

    async def test_rejects_wrong_claims_and_signature(self) -> None:
        cases = (
            self._token(self._claims(iss="https://other.authkit.app")),
            self._token(self._claims(aud="https://other.example/mcp")),
            self._token(self._claims(aud=[self.settings.audience, "https://other.example"])),
            self._token(self._claims(org_id="org_other")),
            self._token({key: value for key, value in self._claims().items() if key != "sub"}),
            self._token(self._claims(exp=int(time()) - 300, iat=int(time()) - 600)),
            self._token(self._claims(nbf=int(time()) + 300)),
            self._token(key=self.other_private_key),
        )
        for token in cases:
            with self.subTest(token_index=cases.index(token)):
                with self.assertRaises(HTTPException) as raised:
                    await self._auth()(self._request(token), token)
                self.assertEqual(raised.exception.status_code, 401)
                self.assertEqual(raised.exception.detail, "invalid_token")

    async def test_rejects_non_rs256_and_missing_kid(self) -> None:
        hs_token = jwt.encode(
            self._claims(),
            "not-a-resource-server-key-at-least-32-bytes",
            algorithm="HS256",
            headers={"kid": "key-1"},
        )
        missing_kid = jwt.encode(self._claims(), self.private_key, algorithm="RS256")
        for token in (hs_token, missing_kid):
            with self.assertRaises(HTTPException):
                await self._auth()(self._request(token), token)

    async def test_requires_exactly_one_authorization_bearer(self) -> None:
        token = self._token()
        requests = (
            Request({"type": "http", "method": "POST", "path": "/v1/models", "headers": []}),
            self._request(token, (b"authorization", f"Bearer {token}".encode())),
            self._request(token, (b"x-litellm-api-key", b"shared-key")),
            Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/v1/models",
                    "headers": [(b"authorization", f"Basic {token}".encode())],
                }
            ),
        )
        for request in requests:
            with self.assertRaises(HTTPException) as raised:
                await self._auth()(request, token)
            self.assertEqual(raised.exception.headers["WWW-Authenticate"], 'Bearer error="invalid_token"')

    async def test_jwks_cache_hit_rotation_and_unknown_key(self) -> None:
        key_sets = [
            {"keys": [self._jwk(self.private_key, "key-1")]},
            {
                "keys": [
                    self._jwk(self.private_key, "key-1"),
                    self._jwk(self.other_private_key, "key-2"),
                ]
            },
        ]

        async def fetcher(url: str) -> dict[str, Any]:
            self.fetch_count += 1
            return key_sets[min(self.fetch_count - 1, 1)]

        cache = WorkOSJWKSCache(self.settings, fetcher=fetcher, monotonic=self.clock)
        auth = WorkOSCustomAuth(self.settings, jwks_cache=cache)
        first = self._token()
        await auth(self._request(first), first)
        await auth(self._request(first), first)
        self.assertEqual(self.fetch_count, 1)

        self.clock.value = self.settings.unknown_kid_refresh_seconds
        rotated = self._token(key=self.other_private_key, kid="key-2")
        await auth(self._request(rotated), rotated)
        self.assertEqual(self.fetch_count, 2)

        unknown = self._token(kid="unknown")
        with self.assertRaises(HTTPException):
            await auth(self._request(unknown), unknown)
        self.assertEqual(self.fetch_count, 2, "unknown-kid refresh must be rate limited")

    async def test_jwks_refresh_failure_fails_closed(self) -> None:
        async def unavailable(url: str) -> dict[str, Any]:
            raise OSError("network unavailable")

        cache = WorkOSJWKSCache(self.settings, fetcher=unavailable, monotonic=self.clock)
        with self.assertRaises(WorkOSAuthenticationError) as raised:
            await cache.get("key-1")
        self.assertEqual(raised.exception.reason, "jwks_unavailable")

    def test_configuration_is_explicit_but_does_not_require_client_or_scope_claims(self) -> None:
        env = {
            "WORKOS_ISSUER": self.settings.issuer,
            "WORKOS_JWKS_URL": self.settings.jwks_url,
            "WORKOS_AUDIENCE": self.settings.audience,
            "WORKOS_ORG_ID": self.settings.org_id,
            "WORKOS_ALLOWED_MODELS": "model-a,model-b",
            "WORKOS_MCP_SERVERS": "microsoft365",
            "WORKOS_MCP_TOOL_PERMISSIONS_JSON": '{"microsoft365":["search_mail"]}',
        }
        parsed = WorkOSSettings.from_environment(env)
        self.assertFalse(any("CLIENT" in key or "SCOPE" in key for key in env))
        self.assertEqual(parsed.mcp_tool_permissions, {"microsoft365": ["search_mail"]})

    def test_missing_runtime_configuration_fails_startup_validation(self) -> None:
        with self.assertRaises(WorkOSConfigurationError):
            WorkOSSettings.from_environment({})

    def test_configuration_rejects_cross_origin_jwks_and_empty_permissions(self) -> None:
        base = {
            "WORKOS_ISSUER": self.settings.issuer,
            "WORKOS_JWKS_URL": "https://attacker.example/oauth2/jwks",
            "WORKOS_AUDIENCE": self.settings.audience,
            "WORKOS_ORG_ID": self.settings.org_id,
            "WORKOS_ALLOWED_MODELS": "model-a",
            "WORKOS_MCP_SERVERS": "microsoft365",
            "WORKOS_MCP_TOOL_PERMISSIONS_JSON": "{}",
        }
        with self.assertRaises(WorkOSConfigurationError):
            WorkOSSettings.from_environment(base)

        base["WORKOS_JWKS_URL"] = self.settings.jwks_url
        with self.assertRaises(WorkOSConfigurationError):
            WorkOSSettings.from_environment(base)


if __name__ == "__main__":
    unittest.main()
