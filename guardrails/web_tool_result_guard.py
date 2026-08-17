"""AiAlchemy Web Tool Result Guard — inspects untrusted web-tool output.

Pipeline position: step 2 (aialchemy-web-tool-result-v1).
Runs after PII input masking, before the trusted provider continuation.

For each function_call_output in a Responses API continuation:
1. Maps call_id → function_call name (trusted provenance).
2. If the tool is in the web-tool allowlist:
   a. Presidio masks PII in the raw output.
   b. Chunks the masked output for Prompt Guard 2.
   c. Runs Prompt Guard on every chunk — one malicious chunk blocks all.
3. Rewrites the output with the Presidio-masked version.

Fail-closed on: missing call_id association, Presidio error, Prompt Guard
error/timeout, chunk limit exceeded.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Optional

from litellm.integrations.custom_guardrail import CustomGuardrail

from guardrails.config import (
    GUARDRAIL_WEB_TOOL_RESULT_NAME,
    MAX_CHUNK_COUNT,
    PROMPT_GUARD_CHUNK_OVERLAP,
    PROMPT_GUARD_CHUNK_SIZE,
    WEB_TOOL_ALLOWLIST,
)
from guardrails.presidio_client import PresidioClient, PresidioError
from guardrails.prompt_guard_client import PromptGuardClient, PromptGuardError

logger = logging.getLogger(__name__)


class AiAlchemyWebToolResultGuard(CustomGuardrail):
    """Inspects untrusted web-tool function_call_output before provider continuation."""

    guardrail_name: str = GUARDRAIL_WEB_TOOL_RESULT_NAME
    event_hook: list[str] = ["pre_call"]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._presidio = PresidioClient()
        self._prompt_guard = PromptGuardClient()

    async def apply_guardrail(
        self,
        inputs: dict[str, Any],
        request_data: dict[str, Any],
        input_type: Literal["request", "response"],
        logging_obj: Optional[Any] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Inspect web-tool results in a Responses API continuation.

        Only processes function_call_output items whose call_id maps to a
        web-tool in the allowlist. Non-web tools pass through untouched.
        """
        if input_type != "request":
            return inputs

        input_items = request_data.get("input")
        if not isinstance(input_items, list):
            return inputs

        # Build call_id → tool_name map from function_call items
        call_id_to_name = self._build_call_id_map(input_items)

        # Process each function_call_output
        for item in input_items:
            if item.get("type") != "function_call_output":
                continue

            call_id = item.get("call_id")
            if not call_id:
                logger.error(
                    "web-tool-guard: function_call_output missing call_id — blocking"
                )
                raise RuntimeError(
                    "Web tool result guard: function_call_output has no call_id — "
                    "cannot determine tool provenance — request blocked"
                )

            tool_name = call_id_to_name.get(call_id)
            if tool_name is None:
                logger.error(
                    "web-tool-guard: no function_call found for call_id — blocking"
                )
                raise RuntimeError(
                    "Web tool result guard: cannot map call_id to a function_call — "
                    "missing association — request blocked"
                )

            # Only inspect web tools
            if tool_name not in WEB_TOOL_ALLOWLIST:
                logger.debug(
                    "web-tool-guard: tool %r not in allowlist — pass-through",
                    tool_name,
                )
                continue

            output = item.get("output", "")
            if not output:
                continue

            logger.debug("web-tool-guard: inspecting output for tool %r", tool_name)

            # Step 1: Presidio masks PII before Prompt Guard sees the text
            masked_output = await self._mask_output(output)

            # Step 2: Chunk the masked output
            chunks = self._chunk_text(masked_output)

            # Step 3: Prompt Guard classifies each chunk
            await self._classify_chunks(chunks, tool_name)

            # Step 4: Rewrite output with Presidio-masked version
            item["output"] = masked_output
            logger.debug("web-tool-guard: output rewritten with masked content")

        return inputs

    def _build_call_id_map(self, input_items: list[dict[str, Any]]) -> dict[str, str]:
        """Map function_call call_id → tool name from the input list.

        Scans ALL items regardless of position to handle out-of-order items.
        """
        mapping: dict[str, str] = {}
        for item in input_items:
            if item.get("type") == "function_call":
                call_id = item.get("call_id")
                name = item.get("name")
                if call_id and name:
                    mapping[call_id] = name
        return mapping

    async def _mask_output(self, text: str) -> str:
        """Run Presidio on raw tool output. Fail closed on error."""
        try:
            result = await self._presidio.analyze_and_anonymize(text)
            if result != text:
                logger.debug("web-tool-guard: Presidio masked PII in tool output")
            return result
        except PresidioError as exc:
            logger.error("web-tool-guard: Presidio failure — blocking request")
            raise RuntimeError(
                "Web tool result guard: Presidio unavailable — request blocked"
            ) from exc
        except Exception as exc:
            logger.error("web-tool-guard: unexpected Presidio error — blocking")
            raise RuntimeError(
                "Web tool result guard: unexpected Presidio failure — request blocked"
            ) from exc

    def _chunk_text(self, text: str) -> list[str]:
        """Divide text into bounded, overlapping chunks for Prompt Guard.

        Uses token-based chunking to respect the model's 512-token context window.
        Raises if the chunk count exceeds the configured maximum.
        Truncation is prohibited per spec — use chunking instead.
        """
        if not text:
            return []

        from guardrails.prompt_guard import ChunkLimitExceeded, chunk_text

        try:
            return chunk_text(
                text,
                chunk_size=PROMPT_GUARD_CHUNK_SIZE,
                overlap=PROMPT_GUARD_CHUNK_OVERLAP,
                max_chunks=MAX_CHUNK_COUNT,
            )
        except ChunkLimitExceeded:
            logger.error(
                "web-tool-guard: text exceeds maximum chunk count — blocking"
            )
            raise RuntimeError(
                f"Web tool result guard: result exceeds chunk limit "
                f"({MAX_CHUNK_COUNT}) — request blocked"
            )

    async def _classify_chunks(
        self, chunks: list[str], tool_name: str
    ) -> None:
        """Run Prompt Guard on each chunk. Block if ANY chunk is malicious.

        Fail closed on Prompt Guard error or timeout.
        """
        for i, chunk in enumerate(chunks):
            try:
                is_malicious = await self._prompt_guard.classify(chunk)
            except PromptGuardError as exc:
                logger.error(
                    "web-tool-guard: Prompt Guard error on chunk %d — blocking", i
                )
                raise RuntimeError(
                    "Web tool result guard: Prompt Guard failure — request blocked"
                ) from exc
            except Exception as exc:
                logger.error(
                    "web-tool-guard: unexpected Prompt Guard error — blocking"
                )
                raise RuntimeError(
                    "Web tool result guard: unexpected Prompt Guard failure — "
                    "request blocked"
                ) from exc

            if is_malicious:
                logger.warning(
                    "web-tool-guard: malicious content detected in chunk %d "
                    "from tool %r — blocking entire continuation",
                    i,
                    tool_name,
                )
                raise RuntimeError(
                    "Web tool result guard: malicious content detected in "
                    f"web tool result from {tool_name!r} — "
                    "entire provider continuation blocked"
                )

        logger.debug(
            "web-tool-guard: all %d chunks classified as benign", len(chunks)
        )
