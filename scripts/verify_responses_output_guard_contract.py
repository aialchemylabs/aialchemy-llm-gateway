#!/usr/bin/env python3
"""Prove the pinned LiteLLM Responses output adapter rewrites final text.

The unit suite stubs LiteLLM so individual guard behavior is deterministic.
This build-time contract test deliberately uses the real pinned handler and a
synthetic Responses payload. It proves that a post-call PII guard can replace
assistant output text without corrupting an adjacent function-call item.
"""

from __future__ import annotations

import asyncio

from guardrails.pii_output_guard import AiAlchemyPiiOutputGuard
from litellm.llms.openai.responses.guardrail_translation.handler import (
    OpenAIResponsesHandler,
)


class _FakePresidio:
    """Deterministic stand-in; transport semantics are tested separately."""

    async def analyze_and_anonymize(self, text: str) -> str:
        return text.replace("john@example.com", "<EMAIL_ADDRESS>")


async def _verify() -> None:
    response = {
        "model": "chatgpt/gpt-5.6-sol",
        "output": [
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "web_search",
                "arguments": '{"q":"safe"}',
                "status": "completed",
            },
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Reach john@example.com",
                        "annotations": [],
                    }
                ],
            },
        ],
    }

    guard = AiAlchemyPiiOutputGuard()
    guard._presidio = _FakePresidio()

    result = await OpenAIResponsesHandler().process_output_response(
        response=response,
        guardrail_to_apply=guard,
        request_data={},
    )

    assert result["output"][0]["arguments"] == '{"q":"safe"}'
    assert result["output"][1]["content"][0]["text"] == "Reach <EMAIL_ADDRESS>"


if __name__ == "__main__":
    asyncio.run(_verify())
    print("responses-output-pii-contract: OK")
