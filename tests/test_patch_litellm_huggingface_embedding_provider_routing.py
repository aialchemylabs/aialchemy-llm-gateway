from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).parents[1]
    / "scripts"
    / "patch_litellm_huggingface_embedding_provider_routing.py"
)

UPSTREAM_IMPORTS = '''import json
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
'''

UPSTREAM_PROCESS_RESPONSE = '''    def _process_embedding_response(
        self,
        embeddings: dict,
        model_response: EmbeddingResponse,
        model: str,
        input: list,
        encoding: Any,
    ) -> EmbeddingResponse:
        output_data: Final = []
        if "similarities" in embeddings:'''

UPSTREAM_AEMBEDDING = '''    async def aembedding(
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

UPSTREAM_EMBEDDING_HEADER = '''    def embedding(
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

# The full remainder of the class (unmodified by the patch) so a written
# fixture file is syntactically valid Python end to end.
UPSTREAM_TAIL = '''

        ## LOGGING
        logging_obj.pre_call(
            input=input,
            api_key=api_key,
            additional_args={
                "complete_input_dict": data,
                "headers": headers,
                "api_base": embed_url,
            },
        )
        ## COMPLETION CALL
        if client is None or not isinstance(client, HTTPHandler):
            client = HTTPHandler(concurrent_limit=1)
        response: Final = client.post(embed_url, headers=headers, data=json.dumps(data))

        ## LOGGING
        logging_obj.post_call(
            input=input,
            api_key=api_key,
            additional_args={"complete_input_dict": data},
            original_response=response,
        )

        embeddings: Final = response.json()

        if "error" in embeddings:
            raise HuggingFaceError(status_code=500, message=embeddings["error"])

        ## PROCESS RESPONSE ##
        return self._process_embedding_response(
            embeddings=embeddings,
            model_response=model_response,
            model=model,
            input=input,
            encoding=encoding,
        )
'''


def build_fixture_source() -> str:
    return (
        UPSTREAM_IMPORTS
        + "\n\nclass HuggingFaceEmbedding:\n"
        + UPSTREAM_PROCESS_RESPONSE
        + "\n            pass\n\n"
        + UPSTREAM_AEMBEDDING
        + "\n        pass\n\n"
        + UPSTREAM_EMBEDDING_HEADER
        + UPSTREAM_TAIL
    )


class PatchLiteLLMHuggingFaceEmbeddingProviderRoutingTests(unittest.TestCase):
    def _load_patch_module(self):
        spec = importlib.util.spec_from_file_location(
            "patch_litellm_huggingface_embedding_provider_routing",
            SCRIPT_PATH,
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_patches_all_four_blocks_and_compiles(self) -> None:
        patch_module = self._load_patch_module()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "handler.py"
            target.write_text(build_fixture_source(), encoding="utf-8")

            self.assertEqual(patch_module.patch_file(target), "patched")
            patched = target.read_text(encoding="utf-8")

            self.assertIn("_resolve_routed_embedding_provider", patched)
            self.assertIn("_fetch_inference_provider_mapping", patched)
            self.assertIn('f"{HF_ROUTER_URL}/{provider}/v1/embeddings"', patched)
            self.assertIn('"data" in embeddings and isinstance(embeddings["data"], list)', patched)
            self.assertIn("prepared_data: dict | None = None", patched)

            # Fixture-plus-patch must remain syntactically valid Python.
            compile(patched, str(target), "exec")

    def test_is_idempotent(self) -> None:
        patch_module = self._load_patch_module()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "handler.py"
            target.write_text(build_fixture_source(), encoding="utf-8")

            self.assertEqual(patch_module.patch_file(target), "patched")
            self.assertEqual(patch_module.patch_file(target), "already-patched")

    def test_fails_closed_when_upstream_shape_changes(self) -> None:
        patch_module = self._load_patch_module()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "handler.py"
            target.write_text("unexpected upstream source", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "found 0"):
                patch_module.patch_file(target)

    def test_fails_closed_when_only_some_blocks_match(self) -> None:
        # Guards against a partial upstream refactor silently producing a
        # half-patched, inconsistent file.
        patch_module = self._load_patch_module()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "handler.py"
            source = build_fixture_source().replace(
                "task_type: Final = optional_params.get(\"input_type\", None)",
                "task_type = optional_params.get(\"input_type\", None)  # upstream renamed Final away",
            )
            target.write_text(source, encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "found 0"):
                patch_module.patch_file(target)

            # Nothing partially written; the file is untouched on failure.
            self.assertEqual(target.read_text(encoding="utf-8"), source)


if __name__ == "__main__":
    unittest.main()
