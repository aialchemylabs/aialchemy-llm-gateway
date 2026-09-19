#!/usr/bin/env python3
"""Backport native TypeSafe pass-through onto LiteLLM 1.101.0.

Runtime changes come from upstream PRs #41607 and #41723:
  deb9d8aeddf085b475109027b8a47d2b2213e11e
  34718f0da692a8665b9f9b70679434ec4396da4a

The stable release eagerly mounts llm_passthrough_router, so the later
upstream lazy-loader, lazy OpenAPI snapshot and separate gateway allowlist
changes do not apply. Keep the upstream authenticated route, shared relay,
credential resolution, response accounting and bundled model prices intact.
Remove this backport once the pinned release includes both upstream fixes.

Every source anchor and generated Python file is validated before any write.
An existing TypeSafe implementation must match this backport exactly; a
partial/changed implementation is rejected rather than silently accepted.
"""

from __future__ import annotations

import argparse
import json
from importlib.util import find_spec
from pathlib import Path


ROUTE_ANCHOR = '@router.api_route(\n    "/milvus/{endpoint:path}",'
ROUTE = '''@router.api_route(
    "/typesafe/{endpoint:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    tags=["TypeSafe AI Pass-through", "pass-through"],
)
async def typesafe_proxy_route(
    endpoint: str,
    request: Request,
    fastapi_response: Response,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
):
    """[Docs](https://docs.litellm.ai/docs/pass_through/typesafe)"""
    base_target_url: Final = get_secret_str("TYPESAFE_API_BASE") or "https://api.typesafe.ai"
    encoded_endpoint: Final = httpx.URL(endpoint).path
    normalized_endpoint: Final = encoded_endpoint if encoded_endpoint.startswith("/") else f"/{encoded_endpoint}"
    base_url: Final = httpx.URL(base_target_url)
    updated_url: Final = base_url.copy_with(
        path=HttpPassThroughEndpointHelpers.join_base_and_endpoint_path(base_url, normalized_endpoint),
    )
    typesafe_api_key: Final = passthrough_endpoint_router.get_credentials(
        custom_llm_provider="typesafe",
        region_name=None,
    )
    endpoint_func: Final = create_pass_through_route(
        endpoint=endpoint,
        target=str(updated_url),
        custom_headers={
            "Authorization": f"Bearer {typesafe_api_key}",
            "Content-Type": "application/json",
        },
        custom_llm_provider="typesafe",
        is_streaming_request=False,
    )
    return await endpoint_func(request, fastapi_response, user_api_key_dict)


'''

LOGGING_ANCHOR = "        elif self.is_vertex_ai_live_route(url_route):\n"
LOGGING_DISPATCH = '''        elif self.is_typesafe_route(custom_llm_provider):
            from .llm_provider_handlers.typesafe_passthrough_logging_handler import (
                TypeSafePassthroughLoggingHandler,
            )

            typesafe_handler_result: Final = TypeSafePassthroughLoggingHandler.typesafe_passthrough_handler(
                httpx_response=httpx_response,
                response_body=response_body if isinstance(response_body, dict) else MappingProxyType({}),
                logging_obj=logging_obj,
                url_route=url_route,
                result=result,
                start_time=start_time,
                end_time=end_time,
                cache_hit=cache_hit,
                request_body=request_body,
                **kwargs,
            )
            standard_logging_response_object = typesafe_handler_result["result"]
            kwargs = typesafe_handler_result["kwargs"]
'''

PREDICATE_ANCHOR = "    def is_langfuse_route(self, url_route: str):\n"
LOGGING_PREDICATE = '''    def is_typesafe_route(self, custom_llm_provider: str | None) -> bool:
        return custom_llm_provider == "typesafe"

'''

# Upstream's complete new handler, with only non-runtime lint annotations removed.
LOGGING_HANDLER = '''from collections.abc import Mapping
from datetime import datetime
from typing import Final

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

import litellm
from litellm.litellm_core_utils.litellm_logging import Logging as LiteLLMLoggingObj
from litellm.litellm_core_utils.litellm_logging import get_standard_logging_object_payload
from litellm.proxy._types import PassThroughEndpointLoggingTypedDict
from litellm.types.utils import ModelResponse, StandardPassThroughResponseObject, Usage


class _TypeSafeUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class _TypeSafeResponse(BaseModel):
    model: str | None = None
    usage: _TypeSafeUsage | None = None


class _RegistryPricing(BaseModel):
    input_cost_per_token: float = 0.0
    output_cost_per_token: float = 0.0


_TYPESAFE_RESPONSE_ADAPTER: Final = TypeAdapter(_TypeSafeResponse)
_REGISTRY_PRICING_ADAPTER: Final = TypeAdapter(_RegistryPricing)


def _parse_typesafe_response(response_body: Mapping[str, object]) -> _TypeSafeResponse:
    try:
        return _TYPESAFE_RESPONSE_ADAPTER.validate_python(response_body)
    except ValidationError:
        return _TypeSafeResponse()


def _pricing_for(model_keys: tuple[str, ...]) -> _RegistryPricing:
    for model_key in model_keys:
        if model_key not in litellm.model_cost:
            continue
        try:
            return _REGISTRY_PRICING_ADAPTER.validate_python(litellm.model_cost[model_key])
        except ValidationError:
            continue
    return _RegistryPricing()


class TypeSafePassthroughLoggingHandler:
    @staticmethod
    def typesafe_passthrough_handler(
        httpx_response: httpx.Response,
        response_body: Mapping[str, object],
        logging_obj: LiteLLMLoggingObj,
        url_route: str,
        result: str,
        start_time: datetime,
        end_time: datetime,
        cache_hit: bool,
        request_body: Mapping[str, object],
        **kwargs: object,
    ) -> PassThroughEndpointLoggingTypedDict:
        response: Final = _parse_typesafe_response(response_body)
        response_model: Final = response.model
        request_model_value: Final = request_body.get("model")
        request_model: Final = request_model_value if isinstance(request_model_value, str) else None
        logged_model: Final = response_model or request_model or "unknown"
        model_name: Final = f"typesafe/{logged_model}"
        usage: Final = response.usage or _TypeSafeUsage()
        input_tokens: Final = usage.input_tokens
        output_tokens: Final = usage.output_tokens
        candidate_model_keys: Final = tuple(
            f"typesafe/{model}" for model in (response_model, request_model) if model is not None
        )
        pricing: Final = _pricing_for(candidate_model_keys)
        response_cost: Final = (
            input_tokens * pricing.input_cost_per_token + output_tokens * pricing.output_cost_per_token
        )
        usage_object: Final = Usage(
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        )
        updated_kwargs: Final = {
            **kwargs,
            "model": model_name,
            "custom_llm_provider": "typesafe",
            "response_cost": response_cost,
            "combined_usage_object": usage_object,
        }
        logging_obj.model_call_details.update(
            model=model_name,
            custom_llm_provider="typesafe",
            response_cost=response_cost,
        )
        standard_logging_object: Final = get_standard_logging_object_payload(
            kwargs=updated_kwargs,
            init_response_obj=ModelResponse(model=model_name, usage=usage_object),
            start_time=start_time,
            end_time=end_time,
            logging_obj=logging_obj,
            status="success",
        )
        return {
            "result": StandardPassThroughResponseObject(response=result),
            "kwargs": {
                **updated_kwargs,
                "standard_logging_object": standard_logging_object,
            },
        }
'''

SOURCE_PATCHES = {
    "proxy/pass_through_endpoints/llm_passthrough_endpoints.py": (
        (ROUTE_ANCHOR, ROUTE + ROUTE_ANCHOR),
    ),
    "proxy/_types.py": (
        ('        "/mistral",\n        "/milvus",',
         '        "/mistral",\n        "/typesafe",\n        "/milvus",'),
    ),
    "types/utils.py": (
        ('            "responses",\n            "ocr",',
         '            "responses",\n            "evaluation",\n            "ocr",'),
    ),
    "proxy/pass_through_endpoints/success_handler.py": (
        ("from datetime import datetime\nfrom typing", "from datetime import datetime\nfrom types import MappingProxyType\nfrom typing"),
        (LOGGING_ANCHOR, LOGGING_DISPATCH + LOGGING_ANCHOR),
        (PREDICATE_ANCHOR, LOGGING_PREDICATE + PREDICATE_ANCHOR),
    ),
}
HANDLER_PATH = "proxy/pass_through_endpoints/llm_provider_handlers/typesafe_passthrough_logging_handler.py"
MODEL_PRICING = {
    "input_cost_per_token": 4.2e-08,
    "litellm_provider": "typesafe",
    "mode": "evaluation",
    "output_cost_per_token": 0.0,
    "source": "https://docs.typesafe.ai/models",
}
MODEL_NAMES = ("jev-1.13.0", "jev-latest", "jev-preview")


def installed_package_path() -> Path:
    spec = find_spec("litellm")
    if spec is None or spec.origin is None:
        raise RuntimeError("The installed litellm package could not be located")
    return Path(spec.origin).parent


def patch_package(package: Path) -> str:
    proxy_source = (package / "proxy/proxy_server.py").read_text(encoding="utf-8")
    if proxy_source.count("app.include_router(llm_passthrough_router)") != 1:
        raise RuntimeError("Expected the LiteLLM 1.101.0 eagerly mounted pass-through router")

    updates = {}
    states = set()
    for relative, replacements in SOURCE_PATCHES.items():
        path = package / relative
        source = path.read_text(encoding="utf-8")
        for old, new in replacements:
            if source.count(new) == 1:
                if source.count(old) != new.count(old):
                    raise RuntimeError(f"Unexpected duplicate original block in {path}")
                states.add("already-patched")
            elif source.count(old) == 1:
                states.add("patched")
                source = source.replace(old, new)
            else:
                raise RuntimeError(f"Expected exactly one original or patched block in {path}")
        # A changed upstream TypeSafe route must not coexist with our route.
        if relative.endswith("llm_passthrough_endpoints.py") and source.count("async def typesafe_proxy_route(") != 1:
            raise RuntimeError(f"Unexpected existing TypeSafe route in {path}")
        compile(source, str(path), "exec")
        updates[path] = source

    handler = package / HANDLER_PATH
    if handler.exists():
        if handler.read_text(encoding="utf-8") != LOGGING_HANDLER:
            raise RuntimeError(f"Unexpected existing TypeSafe handler in {handler}")
        states.add("already-patched")
    else:
        states.add("patched")
    compile(LOGGING_HANDLER, str(handler), "exec")
    updates[handler] = LOGGING_HANDLER

    pricing_path = package / "model_prices_and_context_window_backup.json"
    pricing = json.loads(pricing_path.read_text(encoding="utf-8"))
    for model in MODEL_NAMES:
        key = f"typesafe/{model}"
        if key in pricing:
            if pricing[key] != MODEL_PRICING:
                raise RuntimeError(f"Unexpected existing pricing for {key}")
            states.add("already-patched")
        else:
            pricing[key] = MODEL_PRICING
            states.add("patched")
    updates[pricing_path] = json.dumps(pricing, indent=4, ensure_ascii=False) + "\n"

    if len(states) != 1:
        raise RuntimeError("Partial TypeSafe backport detected; reinstall the pinned package")
    result = states.pop()
    if result == "patched":
        for path, source in updates.items():
            path.write_text(source, encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path, help="Path to the installed litellm package")
    args = parser.parse_args()
    package = args.path if args.path is not None else installed_package_path()
    print(f"{patch_package(package)}: TypeSafe pass-through in {package}")


if __name__ == "__main__":
    main()
