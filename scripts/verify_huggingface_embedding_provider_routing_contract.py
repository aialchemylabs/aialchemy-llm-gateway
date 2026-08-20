#!/usr/bin/env python3
"""Prove the pinned LiteLLM Hugging Face embedding provider-routing patch.

The patch regression test (tests/test_patch_litellm_huggingface_embedding_
provider_routing.py) proves the source-rewrite mechanics against a synthetic
fixture. This build-time contract test deliberately uses the real pinned,
already-patched litellm package and mocked HTTP transport to prove the actual
runtime behavior: provider selection, request route, auth source, and
response transformation for `huggingface/scaleway/Qwen/Qwen3-Embedding-8B`.

Never prints, logs, or returns any credential value or embedding vector
content beyond what is needed to assert routing shape.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import litellm
from litellm.llms.custom_httpx.http_handler import HTTPHandler
from litellm.llms.huggingface.embedding import handler as hf_embedding_handler

EXPECTED_ROUTED_URL = "https://router.huggingface.co/scaleway/v1/embeddings"
EXPECTED_LEGACY_URL = (
    "https://router.huggingface.co/hf-inference/pipeline/feature-extraction/microsoft/codebert-base"
)


def _fake_provider_mapping(model_id: str) -> dict:
    assert model_id == "Qwen/Qwen3-Embedding-8B", model_id
    return {"scaleway": {"providerId": "qwen3-embedding-8b", "status": "live"}}


def verify_routed_provider_selection_and_request() -> None:
    original_fetch = hf_embedding_handler._fetch_inference_provider_mapping
    hf_embedding_handler._fetch_inference_provider_mapping = _fake_provider_mapping
    try:
        fake_client = MagicMock(spec=HTTPHandler)
        fake_response = MagicMock()
        fake_response.json.return_value = {
            "data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}],
            "model": "qwen3-embedding-8b",
        }
        fake_client.post.return_value = fake_response

        response = litellm.embedding(
            model="huggingface/scaleway/Qwen/Qwen3-Embedding-8B",
            input=["contract-test probe text"],
            api_key="hf_contract_test_token_never_real",
            client=fake_client,
        )

        called_url = fake_client.post.call_args.args[0]
        assert called_url == EXPECTED_ROUTED_URL, (
            f"expected routed URL {EXPECTED_ROUTED_URL!r}, got {called_url!r}"
        )

        sent_body = json.loads(fake_client.post.call_args.kwargs["data"])
        assert sent_body == {
            "input": ["contract-test probe text"],
            "model": "qwen3-embedding-8b",
        }, f"unexpected request body shape: {sent_body!r}"

        sent_headers = fake_client.post.call_args.kwargs["headers"]
        auth_header = sent_headers.get("Authorization", "")
        assert auth_header.startswith("Bearer "), "auth must be a Bearer token, HF-token-sourced"
        assert "scw" not in auth_header.lower(), "must never authenticate with a Scaleway credential"

        assert response.data[0]["embedding"] == [0.1, 0.2, 0.3], "response transformation did not round-trip"
        assert response.data[0]["object"] == "embedding"
    finally:
        hf_embedding_handler._fetch_inference_provider_mapping = original_fetch


def verify_non_routed_models_are_unaffected() -> None:
    fake_client = MagicMock(spec=HTTPHandler)
    fake_response = MagicMock()
    fake_response.json.return_value = [[0.4, 0.5, 0.6]]
    fake_client.post.return_value = fake_response

    original_get_task = hf_embedding_handler.get_hf_task_embedding_for_model
    hf_embedding_handler.get_hf_task_embedding_for_model = (
        lambda model, task_type, api_base: "feature-extraction"
    )
    try:
        response = litellm.embedding(
            model="huggingface/microsoft/codebert-base",
            input=["regression probe text"],
            api_key="hf_contract_test_token_never_real",
            client=fake_client,
        )
    finally:
        hf_embedding_handler.get_hf_task_embedding_for_model = original_get_task

    called_url = fake_client.post.call_args.args[0]
    assert called_url == EXPECTED_LEGACY_URL, (
        f"non-routed model must keep hitting the original hf-inference pipeline URL, got {called_url!r}"
    )
    assert response.data[0]["embedding"] == [0.4, 0.5, 0.6], "legacy response parsing regressed"


if __name__ == "__main__":
    verify_routed_provider_selection_and_request()
    verify_non_routed_models_are_unaffected()
    print("huggingface-embedding-provider-routing-contract: OK")
