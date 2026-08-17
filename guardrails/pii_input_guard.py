"""AiAlchemy PII Input Guard — masks PII in provider-bound content.

Pipeline position: step 1 (aialchemy-pii-input-v1).
Runs before Prompt Guard and before the trusted provider sees any content.
Masks configured PII entities using Presidio with non-reversible typed
replacement (e.g. <EMAIL_ADDRESS>, <PERSON>).

Fail-closed: any Presidio error or timeout blocks the request entirely.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Optional

from litellm.integrations.custom_guardrail import CustomGuardrail

from guardrails.config import GUARDRAIL_PII_INPUT_NAME
from guardrails.presidio_client import PresidioClient, PresidioError

logger = logging.getLogger(__name__)


class AiAlchemyPiiInputGuard(CustomGuardrail):
    """Masks PII in all provider-bound text before it leaves LiteLLM."""

    guardrail_name: str = GUARDRAIL_PII_INPUT_NAME
    event_hook: list[str] = ["pre_call"]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._presidio = PresidioClient()

    async def apply_guardrail(
        self,
        inputs: dict[str, Any],
        request_data: dict[str, Any],
        input_type: Literal["request", "response"],
        logging_obj: Optional[Any] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Mask PII in the outgoing request before provider dispatch.

        Handles both Chat Completions (messages list) and Responses API
        (input list with typed items). Hermes sends Responses API items
        which may be:
        - {"role": "user", "content": "..."} (role-based, no "type" field)
        - {"type": "message", "role": "user", "content": [...]}
        - {"type": "function_call_output", ...} (handled by web_tool_result_guard)

        All text-bearing items are masked regardless of format.
        """
        if input_type != "request":
            return inputs

        logger.debug("pii-input-guard: scanning provider-bound content")

        # --- Responses API path: request_data['input'] is a list of items ---
        if "input" in request_data and isinstance(request_data["input"], list):
            await self._mask_responses_input(request_data["input"])

        # --- Chat Completions path: request_data['messages'] ---
        if "messages" in request_data and isinstance(request_data["messages"], list):
            await self._mask_messages(request_data["messages"])

        # --- Responses API instructions field ---
        if "instructions" in request_data and isinstance(request_data["instructions"], str):
            request_data["instructions"] = await self._anonymize_text(
                request_data["instructions"]
            )

        return inputs

    async def _mask_responses_input(self, input_items: list[dict[str, Any]]) -> None:
        """Mask PII in Responses API input items.

        Handles multiple input item formats:
        1. Role-based items: {"role": "user", "content": "text"} (no type field)
        2. Typed message items: {"type": "message", "role": "user", "content": [...]}
        3. String content and list-of-parts content
        4. function_call_output items are SKIPPED (handled by web_tool_result_guard)
        """
        for item in input_items:
            item_type = item.get("type")

            # Skip function_call and function_call_output — handled elsewhere
            if item_type in ("function_call", "function_call_output"):
                continue

            # Extract and mask text content from the item
            content = item.get("content")
            if content is None:
                continue

            if isinstance(content, str):
                # Direct string content (role-based items)
                masked = await self._anonymize_text(content)
                if masked != content:
                    item["content"] = masked
            elif isinstance(content, list):
                # List of content parts (structured items)
                await self._mask_content_parts(content)

    async def _mask_content_parts(self, parts: list[Any]) -> None:
        """Mask PII in a list of content parts."""
        for part in parts:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type", "")
            # Handle all known text part types
            if part_type in ("input_text", "text", "output_text"):
                text = part.get("text", "")
                if text:
                    masked = await self._anonymize_text(text)
                    if masked != text:
                        part["text"] = masked

    async def _mask_messages(self, messages: list[dict[str, Any]]) -> None:
        """Mask PII in Chat Completions messages."""
        for message in messages:
            role = message.get("role", "")
            if role not in ("user", "system", "assistant"):
                continue
            content = message.get("content")
            if content is None:
                continue
            if isinstance(content, str):
                masked = await self._anonymize_text(content)
                if masked != content:
                    message["content"] = masked
            elif isinstance(content, list):
                await self._mask_content_parts(content)

    async def _anonymize_text(self, text: str) -> str:
        """Run Presidio analysis and anonymization on a text block.

        Raises on any Presidio failure to ensure fail-closed behavior.
        Never logs the content or PII entities found.
        """
        try:
            return await self._presidio.analyze_and_anonymize(text)
        except PresidioError as exc:
            logger.error("pii-input-guard: Presidio failure — blocking request")
            raise RuntimeError(
                "PII input guard: Presidio unavailable or errored — request blocked"
            ) from exc
        except Exception as exc:
            logger.error("pii-input-guard: unexpected error — blocking request")
            raise RuntimeError(
                "PII input guard: unexpected failure — request blocked"
            ) from exc
