#!/usr/bin/env python3
"""Exercise native TypeSafe through the real proxy with an offline HTTP transport.

No provider credential, database, external service or SDK is required. The
ASGI app retains its real authentication dependency, using a synthetic master
key only in this process. Every provider request terminates in MockTransport.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

# Keep bundled pricing deterministic and prevent import-time price downloads.
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["LITELLM_TELEMETRY"] = "False"

import httpx

import litellm
from litellm.proxy import proxy_server
from litellm.proxy._types import LiteLLMRoutes, ProxyException
from litellm.proxy.pass_through_endpoints import pass_through_endpoints as relay
from litellm.proxy.pass_through_endpoints.success_handler import PassThroughEndpointLogging


GATEWAY_KEY = "sk-typesafe-gateway-contract-not-a-real-secret"
PROVIDER_KEY = "typesafe-provider-contract-not-a-real-secret"
REQUEST = {
    "model": "jev-latest",
    "state": {"message": "Please route this synthetic support request."},
    "questions": {
        "is_urgent": {"type": "noul", "instructions": "Does this convey urgency?"},
    },
}
RESPONSE = {
    "model": "jev-1.13.0",
    "answers": {"is_urgent": {"type": "noul", "noul": 0.08}},
    "usage": {"input_tokens": 312, "output_tokens": 48},
}


async def verify_authenticated_relay() -> None:
    captured = []
    upstream_status = 200

    def upstream(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        assert request.url.host == "typesafe.contract.invalid", "caller changed the provider host"
        assert request.url.path.startswith("/base/v1/"), "configured base path was lost"
        assert request.headers["authorization"] == f"Bearer {PROVIDER_KEY}", "wrong credential source"
        for header in ("x-litellm-api-key", "x-api-key", "cookie"):
            assert header not in request.headers, f"caller credential header forwarded: {header}"
        assert GATEWAY_KEY not in str(request.headers), "gateway credential reached the provider"
        body = RESPONSE if upstream_status == 200 else {"error": "synthetic upstream rejection"}
        if request.url.path.endswith("/models"):
            body = {"models": ["jev-1.13.0", "jev-latest"]}
        return httpx.Response(upstream_status, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as upstream_client:
        handler = MagicMock()
        handler.client = upstream_client
        with (
            patch.dict(os.environ, {
                "TYPESAFE_API_KEY": PROVIDER_KEY,
                "TYPESAFE_API_BASE": "https://typesafe.contract.invalid/base",
            }),
            patch.object(proxy_server, "master_key", GATEWAY_KEY),
            patch.object(proxy_server, "llm_router", None),
            patch.object(relay, "get_async_httpx_client", return_value=handler),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=proxy_server.app), base_url="http://gateway.contract.invalid"
            ) as client:
                denied = await client.post("/typesafe/v1/systemone", json=REQUEST)
                assert denied.status_code == 401, f"missing key was not denied: {denied.status_code}"
                assert not captured, "unauthenticated request reached the provider"
                # Only the identity-store lookup is stubbed: the actual auth
                # dependency must turn an unknown credential into HTTP 401.
                with (
                    patch.object(proxy_server, "prisma_client", MagicMock()),
                    patch(
                        "litellm.proxy.auth.user_api_key_auth.IdentityStore.resolve",
                        new=AsyncMock(side_effect=ProxyException(
                            message="Synthetic identity store: token does not exist",
                            type="auth_error", code=401, param=None,
                        )),
                    ) as resolve,
                ):
                    denied = await client.post(
                        "/typesafe/v1/systemone", json=REQUEST,
                        headers={"Authorization": "Bearer sk-invalid-contract-key"},
                    )
                    assert denied.status_code == 401, f"unknown key was not denied: {denied.status_code}"
                    resolve.assert_awaited()
                    assert not captured, "invalid gateway key reached the provider"
                print("typesafe-auth: OK (missing and invalid gateway keys denied before upstream)")

                for method in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                    response = await client.request(
                        method,
                        "/typesafe/v1/systemone?trace=contract",
                        json=None if method in ("GET", "DELETE") else REQUEST,
                        headers={
                            "Authorization": f"Bearer {GATEWAY_KEY}",
                            "x-api-key": "caller-key-must-not-be-forwarded",
                            "Cookie": "session=caller-cookie-must-not-be-forwarded",
                        },
                    )
                    assert response.status_code == 200, f"{method} relay failed: {response.status_code} {response.text}"
                    assert response.json() == RESPONSE, "typed provider response was transformed"
                    sent = captured[-1]
                    assert sent.method == method
                    assert dict(sent.url.params) == {"trace": "contract"}, "query parameters were lost"
                    if method not in ("GET", "DELETE"):
                        assert json.loads(sent.content) == REQUEST, "typed request body was transformed"

                discovery = await client.get(
                    "/typesafe/v1/models",
                    headers={"x-litellm-api-key": f"Bearer {GATEWAY_KEY}"},
                )
                assert discovery.status_code == 200, "model discovery failed"
                assert discovery.json() == {"models": ["jev-1.13.0", "jev-latest"]}
                assert captured[-1].method == "GET" and not captured[-1].content

                upstream_status = 429
                error = await client.post(
                    "/typesafe/v1/systemone", json=REQUEST,
                    headers={"Authorization": f"Bearer {GATEWAY_KEY}"},
                )
                assert error.status_code == 429, "upstream error status was hidden"
                assert "synthetic upstream rejection" in error.text, "upstream error detail was lost"
                # Background logging tasks must finish before their event loop closes.
                await asyncio.sleep(0)

    print("typesafe-relay: OK (all five methods, body/query/base path, model discovery, upstream 429)")
    print("typesafe-credentials: OK (provider key only; caller authorization, API keys and cookies isolated)")


def verify_usage_accounting() -> None:
    assert "/typesafe" in LiteLLMRoutes.mapped_pass_through_routes.value
    for model in ("jev-1.13.0", "jev-latest", "jev-preview"):
        pricing = litellm.model_cost[f"typesafe/{model}"]
        assert pricing["litellm_provider"] == "typesafe"
        assert pricing["input_cost_per_token"] == 4.2e-08
        assert pricing["output_cost_per_token"] == 0

    def normalize(body: object, request: dict) -> tuple[dict, dict]:
        logging_obj = MagicMock()
        logging_obj.model_call_details = {}
        result = PassThroughEndpointLogging().normalize_llm_passthrough_logging_payload(
            httpx_response=httpx.Response(
                200, request=httpx.Request("POST", "https://api.typesafe.ai/v1/systemone"), json=body,
            ),
            response_body=body,
            request_body=request,
            logging_obj=logging_obj,
            url_route="https://api.typesafe.ai/v1/systemone",
            result=json.dumps(body),
            start_time=datetime.now(), end_time=datetime.now(), cache_hit=False,
            custom_llm_provider="typesafe",
        )
        return result["kwargs"], logging_obj.model_call_details

    kwargs, details = normalize(RESPONSE, REQUEST)
    expected = 312 * litellm.model_cost["typesafe/jev-1.13.0"]["input_cost_per_token"]
    assert kwargs["model"] == "typesafe/jev-1.13.0", "versioned response model was not logged"
    assert kwargs["custom_llm_provider"] == "typesafe"
    assert abs(kwargs["response_cost"] - expected) < 1e-12, "token cost differs from bundled pricing"
    assert details["model"] == kwargs["model"] and details["response_cost"] == expected
    usage = kwargs["combined_usage_object"]
    assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (312, 48, 360)

    fallback, _ = normalize({"usage": {"input_tokens": 10}}, {"model": "jev-latest"})
    assert fallback["model"] == "typesafe/jev-latest" and fallback["response_cost"] > 0
    unknown_version, _ = normalize({"model": "jev-future", "usage": {"input_tokens": 10}}, REQUEST)
    assert unknown_version["model"] == "typesafe/jev-future"
    assert unknown_version["response_cost"] == fallback["response_cost"], "alias price fallback failed"
    for response, request in (({}, {}), ({"model": "jev-1.13.0"}, REQUEST), ({"usage": "invalid"}, REQUEST), ([], REQUEST)):
        unpriced, _ = normalize(response, request)
        assert unpriced["response_cost"] == 0, "missing or malformed usage was charged"
    print("typesafe-accounting: OK (versioned model, 312 input + 48 output tokens, cost $0.000013104)")


def main() -> None:
    asyncio.run(verify_authenticated_relay())
    verify_usage_accounting()
    print("typesafe-passthrough-contract: OK (offline; synthetic credentials and data only)")


if __name__ == "__main__":
    main()
