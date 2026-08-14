#!/usr/bin/env python3
"""Fail the image build if provider streaming translation regresses."""

from litellm.llms.chatgpt.responses.transformation import (
    ChatGPTResponsesAPIConfig,
)
from litellm.llms.anthropic.experimental_pass_through.utils import (
    normalize_reasoning_effort_value,
)
from litellm.llms.vertex_ai.common_utils import _get_gemini_url
from litellm.types.router import GenericLiteLLMParams


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

    normalized_effort = normalize_reasoning_effort_value(
        effort="xhigh",
        model="chatgpt/gpt-5.6-sol",
        custom_llm_provider=None,
    )
    if normalized_effort != "xhigh":
        raise RuntimeError(
            f"ChatGPT subscription xhigh effort was changed to {normalized_effort!r}"
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


def main() -> None:
    verify_chatgpt_subscription_streaming()
    verify_gemini_streaming()
    print(
        "verified: ChatGPT native SSE/xhigh effort and Gemini "
        "streamGenerateContent"
    )


if __name__ == "__main__":
    main()
