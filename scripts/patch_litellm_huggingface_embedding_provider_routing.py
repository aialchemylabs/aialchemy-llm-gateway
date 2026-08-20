#!/usr/bin/env python3
"""Route ``huggingface/<provider>/<org>/<model>`` embeddings through the
provider named in the model string instead of always calling ``hf-inference``.

Upstream bug: https://github.com/BerriAI/litellm/issues/34503 (open fix:
https://github.com/BerriAI/litellm/pull/34540, which fixes only
``litellm.embedding(model="scaleway/...")`` — the direct-Scaleway-key path,
not the ``huggingface/<provider>/...`` HF-token path this project requires).

LiteLLM's completion() path already resolves a routed HF provider (chat/
transformation.py's ``_get_complete_url`` / ``transform_request``, which call
the shared ``_fetch_inference_provider_mapping`` helper in
``huggingface/common_utils.py``). The embedding() path has no equivalent: its
handler hard-codes ``https://router.huggingface.co/hf-inference/pipeline/
{task}/{model}`` and never inspects a provider segment in ``model`` at all, so
``huggingface/scaleway/Qwen/Qwen3-Embedding-8B`` silently falls through to
`hf-inference`` — which does not serve this model, hence the empty/failed
response observed in production.

This patch adds routed-provider handling to
``litellm/llms/huggingface/embedding/handler.py`` only, reusing the existing
``_fetch_inference_provider_mapping`` helper (no new HTTP client, no new
provider mapping cache). For a routed model (3+ ``/``-separated segments) it:

  * builds the router URL as ``https://router.huggingface.co/{provider}/v1/
    embeddings`` -- the exact route ``huggingface_hub``'s own
    ``ScalewayFeatureExtractionTask._prepare_route`` returns, confirmed against
    huggingface_hub's installed provider implementation
    (inference/_providers/scaleway.py) and HF's own Scaleway provider docs
    (https://huggingface.co/docs/inference-providers/en/providers/scaleway);
  * sends an OpenAI-embeddings-shaped payload with ``model`` set to the
    provider's own model id from the fetched provider mapping (e.g.
    ``qwen3-embedding-8b``), not the HF org/repo path;
  * parses the OpenAI-embeddings-shaped ``{"data": [{"embedding": [...]}]}``
    response Scaleway returns, alongside (not instead of) the existing
    HF-shaped response parsing used by every other provider this handler
    already supports.

Non-routed models (bare ids, ``http(s)://`` urls, explicit ``hf-inference``)
are completely unaffected -- every existing branch and test for those stays
exactly as upstream ships it.
"""

from __future__ import annotations

import argparse
from importlib.util import find_spec
from pathlib import Path


OLD_IMPORTS = """import json
import os
from collections.abc import Callable
from typing import Any, Final, Literal, get_args

import httpx

import litellm
from litellm.litellm_core_utils.litellm_logging import Logging as LiteLLMLoggingObj
from litellm.llms.custom_httpx.http_handler import (
    AsyncHTTPHandler,
    HTTPHandler,
    get_async_httpx_client,
)
from litellm.types.utils import EmbeddingResponse

from ...base import BaseLLM
from ..common_utils import HuggingFaceError
from .transformation import HuggingFaceEmbeddingConfig

config: Final = HuggingFaceEmbeddingConfig()

HF_HUB_URL: Final = "https://huggingface.co"
"""

NEW_IMPORTS = """import json
import os
from collections.abc import Callable
from typing import Any, Final, Literal, get_args

import httpx

import litellm
from litellm.litellm_core_utils.litellm_logging import Logging as LiteLLMLoggingObj
from litellm.llms.custom_httpx.http_handler import (
    AsyncHTTPHandler,
    HTTPHandler,
    get_async_httpx_client,
)
from litellm.types.utils import EmbeddingResponse

from ...base import BaseLLM
from ..common_utils import HuggingFaceError, _fetch_inference_provider_mapping
from .transformation import HuggingFaceEmbeddingConfig

config: Final = HuggingFaceEmbeddingConfig()

HF_HUB_URL: Final = "https://huggingface.co"
HF_ROUTER_URL: Final = "https://router.huggingface.co"


def _resolve_routed_embedding_provider(model: str) -> tuple[str, str] | None:
    \"\"\"Return ``(provider, provider_model_id)`` for a routed HF model string.

    A routed model string has the shape ``<provider>/<hf_org>/<hf_repo>``
    (three or more ``/``-separated segments), e.g.
    ``scaleway/Qwen/Qwen3-Embedding-8B``. Bare model ids
    (``Qwen/Qwen3-Embedding-8B``), explicit ``hf-inference`` calls, and
    ``http(s)://`` URLs all have at most two segments (or none) and return
    ``None`` unchanged, so every existing embedding() branch keeps its current
    behavior.

    Reuses the same provider-mapping lookup completion() already relies on
    (``huggingface/chat/transformation.py`` imports this identical helper), so
    this adds no new HTTP client or cache.
    \"\"\"
    if model.startswith(("http://", "https://")):
        return None
    parts = model.split("/", 1)
    if len(parts) != 2 or "/" not in parts[1]:
        return None
    provider, hf_model_id = parts[0], parts[1]
    if provider == "hf-inference":
        return None
    provider_mapping = _fetch_inference_provider_mapping(hf_model_id)
    if provider not in provider_mapping:
        raise HuggingFaceError(
            status_code=404,
            message=f"Model {hf_model_id} is not supported for provider {provider}",
        )
    return provider, provider_mapping[provider]["providerId"]
"""

OLD_PROCESS_RESPONSE = '''    def _process_embedding_response(
        self,
        embeddings: dict,
        model_response: EmbeddingResponse,
        model: str,
        input: list,
        encoding: Any,
    ) -> EmbeddingResponse:
        output_data: Final = []
        if "similarities" in embeddings:'''

NEW_PROCESS_RESPONSE = '''    def _process_embedding_response(
        self,
        embeddings: dict,
        model_response: EmbeddingResponse,
        model: str,
        input: list,
        encoding: Any,
    ) -> EmbeddingResponse:
        output_data: Final = []
        if "data" in embeddings and isinstance(embeddings["data"], list):
            # OpenAI-embeddings-shaped response, returned by routed HF
            # Inference Providers such as Scaleway
            # (https://huggingface.co/docs/inference-providers/en/providers/scaleway),
            # not the raw HF pipeline shape the branches below parse.
            for idx, item in enumerate(embeddings["data"]):
                output_data.append(
                    {
                        "object": "embedding",
                        "index": item.get("index", idx),
                        "embedding": item["embedding"],
                    }
                )
        elif "similarities" in embeddings:'''

OLD_EMBEDDING_METHOD_HEADER = '''    def embedding(
        self,
        model: str,
        input: list,
        model_response: EmbeddingResponse,
        optional_params: dict,
        litellm_params: dict,
        logging_obj: LiteLLMLoggingObj,
        encoding: Callable,
        api_key: str | None = None,
        api_base: str | None = None,
        timeout: float | httpx.Timeout = httpx.Timeout(None),
        aembedding: bool | None = None,
        client: HTTPHandler | AsyncHTTPHandler | None = None,
        headers={},
    ) -> EmbeddingResponse:
        super().embedding()
        headers = config.validate_environment(
            api_key=api_key,
            headers=headers,
            model=model,
            optional_params=optional_params,
            messages=[],
            litellm_params=litellm_params,
        )
        task_type: Final = optional_params.get("input_type", None)
        task: Final = get_hf_task_embedding_for_model(model=model, task_type=task_type, api_base=HF_HUB_URL)
        # print_verbose(f"{model}, {task}")
        embed_url = ""
        if model.startswith(("http://", "https://")):
            embed_url = model
        elif api_base:
            embed_url = api_base
        elif "HF_API_BASE" in os.environ:
            embed_url = os.getenv("HF_API_BASE", "")
        elif "HUGGINGFACE_API_BASE" in os.environ:
            embed_url = os.getenv("HUGGINGFACE_API_BASE", "")
        else:
            embed_url = f"https://router.huggingface.co/hf-inference/pipeline/{task}/{model}"

        ## ROUTING ##
        if aembedding is True:
            return self.aembedding(
                input=input,
                model_response=model_response,
                timeout=timeout,
                logging_obj=logging_obj,
                headers=headers,
                api_base=embed_url,
                api_key=api_key,
                client=client if isinstance(client, AsyncHTTPHandler) else None,
                model=model,
                optional_params=optional_params,
                encoding=encoding,
            )

        ## TRANSFORMATION ##

        data: Final = self._transform_input(
            input=input,
            model=model,
            call_type="sync",
            optional_params=optional_params,
            embed_url=embed_url,
        )'''

NEW_EMBEDDING_METHOD_HEADER = '''    def embedding(
        self,
        model: str,
        input: list,
        model_response: EmbeddingResponse,
        optional_params: dict,
        litellm_params: dict,
        logging_obj: LiteLLMLoggingObj,
        encoding: Callable,
        api_key: str | None = None,
        api_base: str | None = None,
        timeout: float | httpx.Timeout = httpx.Timeout(None),
        aembedding: bool | None = None,
        client: HTTPHandler | AsyncHTTPHandler | None = None,
        headers={},
    ) -> EmbeddingResponse:
        super().embedding()
        headers = config.validate_environment(
            api_key=api_key,
            headers=headers,
            model=model,
            optional_params=optional_params,
            messages=[],
            litellm_params=litellm_params,
        )
        routed_provider: Final = None if api_base else _resolve_routed_embedding_provider(model)
        if routed_provider is not None:
            provider, provider_model_id = routed_provider
            embed_url = f"{HF_ROUTER_URL}/{provider}/v1/embeddings"
            data: Final = {"input": input, "model": provider_model_id}
            if len(optional_params.keys()) > 0:
                data.update(optional_params)

            if aembedding is True:
                return self.aembedding(
                    input=input,
                    model_response=model_response,
                    timeout=timeout,
                    logging_obj=logging_obj,
                    headers=headers,
                    api_base=embed_url,
                    api_key=api_key,
                    client=client if isinstance(client, AsyncHTTPHandler) else None,
                    model=model,
                    optional_params=optional_params,
                    encoding=encoding,
                    prepared_data=data,
                )
        else:
            task_type: Final = optional_params.get("input_type", None)
            task: Final = get_hf_task_embedding_for_model(model=model, task_type=task_type, api_base=HF_HUB_URL)
            # print_verbose(f"{model}, {task}")
            embed_url = ""
            if model.startswith(("http://", "https://")):
                embed_url = model
            elif api_base:
                embed_url = api_base
            elif "HF_API_BASE" in os.environ:
                embed_url = os.getenv("HF_API_BASE", "")
            elif "HUGGINGFACE_API_BASE" in os.environ:
                embed_url = os.getenv("HUGGINGFACE_API_BASE", "")
            else:
                embed_url = f"https://router.huggingface.co/hf-inference/pipeline/{task}/{model}"

            ## ROUTING ##
            if aembedding is True:
                return self.aembedding(
                    input=input,
                    model_response=model_response,
                    timeout=timeout,
                    logging_obj=logging_obj,
                    headers=headers,
                    api_base=embed_url,
                    api_key=api_key,
                    client=client if isinstance(client, AsyncHTTPHandler) else None,
                    model=model,
                    optional_params=optional_params,
                    encoding=encoding,
                )

            ## TRANSFORMATION ##

            data = self._transform_input(
                input=input,
                model=model,
                call_type="sync",
                optional_params=optional_params,
                embed_url=embed_url,
            )'''

OLD_AEMBEDDING_METHOD = '''    async def aembedding(
        self,
        model: str,
        input: list,
        model_response: litellm.utils.EmbeddingResponse,
        timeout: float | httpx.Timeout,
        logging_obj: LiteLLMLoggingObj,
        optional_params: dict,
        api_base: str,
        api_key: str | None,
        headers: dict,
        encoding: Callable,
        client: AsyncHTTPHandler | None = None,
    ):
        ## TRANSFORMATION ##
        data: Final = self._transform_input(
            input=input,
            model=model,
            call_type="sync",
            optional_params=optional_params,
            embed_url=api_base,
        )'''

NEW_AEMBEDDING_METHOD = '''    async def aembedding(
        self,
        model: str,
        input: list,
        model_response: litellm.utils.EmbeddingResponse,
        timeout: float | httpx.Timeout,
        logging_obj: LiteLLMLoggingObj,
        optional_params: dict,
        api_base: str,
        api_key: str | None,
        headers: dict,
        encoding: Callable,
        client: AsyncHTTPHandler | None = None,
        prepared_data: dict | None = None,
    ):
        ## TRANSFORMATION ##
        data: Final = (
            prepared_data
            if prepared_data is not None
            else self._transform_input(
                input=input,
                model=model,
                call_type="sync",
                optional_params=optional_params,
                embed_url=api_base,
            )
        )'''


def installed_handler_path() -> Path:
    spec = find_spec("litellm")
    if spec is None or spec.origin is None:
        raise RuntimeError("The installed litellm package could not be located")
    return (
        Path(spec.origin).parent
        / "llms"
        / "huggingface"
        / "embedding"
        / "handler.py"
    )


def patch_file(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    if "_resolve_routed_embedding_provider" in source:
        return "already-patched"

    blocks = [
        ("imports", OLD_IMPORTS, NEW_IMPORTS),
        ("_process_embedding_response", OLD_PROCESS_RESPONSE, NEW_PROCESS_RESPONSE),
        ("aembedding", OLD_AEMBEDDING_METHOD, NEW_AEMBEDDING_METHOD),
        ("embedding", OLD_EMBEDDING_METHOD_HEADER, NEW_EMBEDDING_METHOD_HEADER),
    ]
    for name, old, new in blocks:
        matches = source.count(old)
        if matches != 1:
            raise RuntimeError(
                f"Expected exactly one {name!r} block in {path}; found {matches}"
            )
        source = source.replace(old, new)

    path.write_text(source, encoding="utf-8")
    return "patched"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path)
    args = parser.parse_args()
    path = args.path if args.path is not None else installed_handler_path()
    result = patch_file(path)
    print(f"{result}: {path}")


if __name__ == "__main__":
    main()
