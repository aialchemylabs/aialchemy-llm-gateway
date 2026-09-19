#!/usr/bin/env python3
"""Fail the image build if provider streaming translation regresses."""

import asyncio
from types import SimpleNamespace

from litellm.completion_extras.litellm_responses_transformation.handler import (
    ResponsesToCompletionBridgeHandler,
)
from litellm.llms.chatgpt.responses.transformation import (
    ChatGPTResponsesAPIConfig,
)
from litellm.llms.anthropic.experimental_pass_through.utils import (
    normalize_reasoning_effort_value,
)
from litellm.llms.vertex_ai.common_utils import _get_gemini_url
from litellm.types.router import GenericLiteLLMParams
from litellm.types.llms.openai import ResponsesAPIResponse


def verify_chatgpt_subscription_streaming() -> None:
    config = ChatGPTResponsesAPIConfig()
    for model in ("gpt-5.6-sol", "future-chatgpt-model"):
        if config.should_fake_stream(
            model=model,
            stream=True,
            custom_llm_provider="chatgpt",
        ):
            raise RuntimeError(f"ChatGPT native streaming disabled for {model}")

    request = config.transform_responses_api_request(
        model="gpt-5.6-sol",
        input=[{"role": "user", "content": "stream contract probe"}],
        response_api_optional_request_params={},
        litellm_params=GenericLiteLLMParams(),
        headers={},
    )
    if request.get("stream") is not True:
        raise RuntimeError("ChatGPT subscription request does not force stream=true")

    for effort in ("xhigh", "max"):
        for model, provider in (
            ("chatgpt/future-chatgpt-model", None),
            ("future-chatgpt-model", "chatgpt"),
        ):
            normalized_effort = normalize_reasoning_effort_value(
                effort=effort, model=model, custom_llm_provider=provider,
            )
            if normalized_effort != effort:
                raise RuntimeError(
                    f"ChatGPT subscription {effort} was changed to {normalized_effort!r}"
                )


def verify_gemini_streaming() -> None:
    url, endpoint = _get_gemini_url(
        mode="chat",
        model="gemini-3.6-flash",
        stream=True,
    )
    if endpoint != "streamGenerateContent":
        raise RuntimeError(f"Gemini streaming endpoint is {endpoint!r}")
    if not url.endswith(":streamGenerateContent?alt=sse"):
        raise RuntimeError(f"Gemini streaming URL is {url!r}")


def verify_completed_sse_output() -> None:
    """Exercise both installed collectors, including incomplete-stream rejection."""
    first = {"id": "message-0", "type": "message", "content": []}
    second = {"id": "message-1", "type": "message", "content": []}
    events = [
        SimpleNamespace(type="response.output_item.done", output_index=1, item=second),
        SimpleNamespace(type="response.output_item.done", output_index=0, item=first),
        SimpleNamespace(type="response.output_text.delta", output_index=2, item={}),
    ]

    class Stream:
        def __init__(self, output, complete=True):
            self.completed_response = (
                SimpleNamespace(response=ResponsesAPIResponse.model_construct(output=output))
                if complete
                else None
            )
            self._hidden_params = {"custom_llm_provider": "chatgpt"}

        def __iter__(self):
            return iter(events)

        async def __aiter__(self):
            for event in events:
                yield event

    handler = ResponsesToCompletionBridgeHandler()
    for asynchronous in (False, True):
        def collect(stream):
            if asynchronous:
                return asyncio.run(handler._collect_response_from_stream_async(stream))
            return handler._collect_response_from_stream(stream)

        recovered = collect(Stream([]))
        if recovered.output != [first, second]:
            raise RuntimeError("Completed SSE items were lost, reordered, or mixed with deltas")
        if recovered._hidden_params.get("custom_llm_provider") != "chatgpt":
            raise RuntimeError("SSE recovery dropped provider metadata")
        if collect(Stream([second])).output != [second]:
            raise RuntimeError("SSE recovery replaced authoritative completed output")
        try:
            collect(Stream([], complete=False))
        except ValueError as error:
            if "without a completed response" not in str(error):
                raise
        else:
            raise RuntimeError("Incomplete SSE stream was accepted as a success")


def main() -> None:
    verify_chatgpt_subscription_streaming()
    verify_gemini_streaming()
    verify_completed_sse_output()
    print(
        "verified: ChatGPT native SSE/xhigh/max effort, completed output recovery "
        "(sync/async), incomplete-stream rejection and Gemini streamGenerateContent"
    )


if __name__ == "__main__":
    main()
