"""AiAlchemy Web Tool Result Guard — inspects untrusted web-tool output.

Pipeline position: step 2 (aialchemy-web-tool-result-v1).
Runs after PII input masking, before the trusted provider continuation.

For each function_call_output in a Responses API continuation:
1. Maps call_id → function_call name (trusted provenance).
2. If the tool is in the web-tool allowlist:
   a. Presidio masks PII in the raw output.
   b. Chunks the masked output for Prompt Guard 2 (token-based).
   c. Runs Prompt Guard on every chunk — one malicious chunk blocks all.
3. Rewrites the output with the Presidio-masked version.

Fail-closed on: missing call_id association (including previous_response_id
continuations where the function_call is not in this request), Presidio error,
Prompt Guard error/timeout, chunk limit exceeded, non-scannable structured output.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any, Literal, Optional

from litellm.integrations.custom_guardrail import CustomGuardrail

from guardrails.config import (
    GUARDRAIL_WEB_TOOL_RESULT_NAME,
    MAX_CHUNK_COUNT,
    PROMPT_GUARD_CHUNK_OVERLAP,
    PROMPT_GUARD_CHUNK_SIZE,
    PROMPT_GUARD_MAX_RESULT_BYTES,
    PROMPT_GUARD_TOTAL_TIMEOUT_SECONDS,
    WEB_TOOL_ALLOWLIST,
)
from guardrails.presidio_client import PresidioClient, PresidioError
from guardrails.prompt_guard_client import PromptGuardClient, PromptGuardError
from guardrails.responses_tool_output import (
    ToolOutputShapeError,
    collect_text_parts,
    replace_text_parts,
    total_utf8_bytes,
)

# Token-based chunking — fail closed if import fails (dependency missing).
try:
    from guardrails.prompt_guard import ChunkLimitExceeded, chunk_text, get_tokenizer
except ImportError as _import_err:
    _chunk_import_error: Optional[ImportError] = _import_err
    ChunkLimitExceeded = None  # type: ignore[assignment, misc]
    chunk_text = None  # type: ignore[assignment]
    get_tokenizer = None  # type: ignore[assignment]
else:
    _chunk_import_error = None

logger = logging.getLogger(__name__)


class AiAlchemyWebToolResultGuard(CustomGuardrail):
    """Inspects untrusted web-tool function_call_output before provider continuation.

    Operates on raw request_data['input'] mutation — correct for function_call_output
    items which LiteLLM does not extract into inputs['texts'].
    """

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

        Mutates request_data['input'] in place — this is the correct path for
        function_call_output payloads.
        """
        if input_type != "request":
            return inputs

        input_items = request_data.get("input")
        if not isinstance(input_items, list):
            return inputs

        # Build call_id → tool_name map from function_call items in this request
        call_id_to_name = self._build_call_id_map(input_items)

        # Process each function_call_output. A call may produce exactly one
        # result; duplicates are ambiguous/replay-prone and fail closed.
        output_call_ids: set[str] = set()
        for item in input_items:
            if not isinstance(item, dict):
                raise RuntimeError(
                    "Web tool result guard: malformed Responses input item — "
                    "request blocked"
                )
            if item.get("type") != "function_call_output":
                continue

            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id.strip():
                logger.error(
                    "web-tool-guard: function_call_output missing call_id — blocking"
                )
                raise RuntimeError(
                    "Web tool result guard: function_call_output has no call_id — "
                    "cannot determine tool provenance — request blocked"
                )
            call_id = call_id.strip()
            if call_id in output_call_ids:
                logger.error(
                    "web-tool-guard: duplicate function_call_output call_id — blocking"
                )
                raise RuntimeError(
                    "Web tool result guard: duplicate function_call_output call_id — "
                    "request blocked"
                )
            output_call_ids.add(call_id)

            tool_name = call_id_to_name.get(call_id)
            if tool_name is None:
                # This happens in previous_response_id continuations where the
                # function_call that produced this call_id is not in the current
                # request input list. We cannot verify tool provenance → fail closed.
                logger.error(
                    "web-tool-guard: no function_call found for output call_id in "
                    "current request input — cannot verify tool association "
                    "(previous_response_id continuation?) — blocking",
                )
                raise RuntimeError(
                    "Web tool result guard: cannot map call_id to a function_call — "
                    "association missing from request (possible previous_response_id "
                    "continuation) — request blocked"
                )

            # Only inspect web tools
            if tool_name not in WEB_TOOL_ALLOWLIST:
                logger.debug(
                    "web-tool-guard: tool %r not in allowlist — pass-through",
                    tool_name,
                )
                continue

            output = item.get("output", "")
            try:
                text_parts = collect_text_parts(output)
            except ToolOutputShapeError as exc:
                logger.error(
                    "web-tool-guard: unsupported output shape from tool %r — blocking",
                    tool_name,
                )
                raise RuntimeError(
                    f"Web tool result guard: {exc} — request blocked"
                ) from exc

            if total_utf8_bytes(text_parts) > PROMPT_GUARD_MAX_RESULT_BYTES:
                logger.error(
                    "web-tool-guard: result exceeds raw size limit — blocking"
                )
                raise RuntimeError(
                    "Web tool result guard: result exceeds the configured size limit — "
                    "request blocked"
                )

            if not any(part.text for part in text_parts):
                continue

            logger.debug("web-tool-guard: inspecting output for tool %r", tool_name)

            # Step 1: Presidio masks PII before Prompt Guard sees the text
            masked_parts = [
                await self._mask_output(part.text) if part.text else part.text
                for part in text_parts
            ]
            masked_text = "\n".join(text for text in masked_parts if text)

            # Step 2: Chunk the masked output (token-based)
            chunks = await self._chunk_text(masked_text)

            # Step 3: Prompt Guard classifies each chunk
            await self._classify_chunks(chunks, tool_name)

            # Step 4: Rewrite output with Presidio-masked version
            try:
                item["output"] = replace_text_parts(
                    output, text_parts, masked_parts
                )
            except ToolOutputShapeError as exc:
                raise RuntimeError(
                    "Web tool result guard: output changed during rewrite — "
                    "request blocked"
                ) from exc
            logger.debug("web-tool-guard: output rewritten with masked content")

        return inputs

    def _build_call_id_map(self, input_items: list[dict[str, Any]]) -> dict[str, str]:
        """Map function_call call_id → tool name from the input list.

        Scans ALL items regardless of position to handle out-of-order items.
        Only covers function_call items present in THIS request — continuations
        using previous_response_id will NOT have the originating function_call here.
        """
        mapping: dict[str, str] = {}
        for item in input_items:
            if not isinstance(item, dict):
                raise RuntimeError(
                    "Web tool result guard: malformed Responses input item — "
                    "request blocked"
                )
            if item.get("type") == "function_call":
                call_id = item.get("call_id")
                name = item.get("name")
                if (
                    not isinstance(call_id, str)
                    or not call_id.strip()
                    or not isinstance(name, str)
                    or not name.strip()
                ):
                    raise RuntimeError(
                        "Web tool result guard: malformed function_call provenance — "
                        "request blocked"
                    )
                call_id = call_id.strip()
                if call_id in mapping:
                    raise RuntimeError(
                        "Web tool result guard: duplicate function_call call_id — "
                        "request blocked"
                    )
                mapping[call_id] = name.strip()
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

    async def _chunk_text(self, text: str) -> list[str]:
        """Divide text into bounded, overlapping chunks for Prompt Guard.

        Uses token-based chunking to respect the model's 512-token context window.
        Fails closed if chunking module is unavailable or chunk limit exceeded.
        """
        if not text:
            return []

        # Fail closed if chunk_text could not be imported
        if chunk_text is None or get_tokenizer is None:
            logger.error(
                "web-tool-guard: chunk_text import failed (%s) — blocking",
                _chunk_import_error,
            )
            raise RuntimeError(
                "Web tool result guard: chunking module unavailable — "
                "cannot process web tool output — request blocked"
            )

        try:
            loop = asyncio.get_running_loop()
            tokenizer = await loop.run_in_executor(None, get_tokenizer)
            return await loop.run_in_executor(
                None,
                functools.partial(
                    chunk_text,
                    text,
                    chunk_size=PROMPT_GUARD_CHUNK_SIZE,
                    overlap=PROMPT_GUARD_CHUNK_OVERLAP,
                    max_chunks=MAX_CHUNK_COUNT,
                    tokenizer=tokenizer,
                ),
            )
        except Exception as exc:
            if ChunkLimitExceeded is not None and isinstance(
                exc, ChunkLimitExceeded
            ):
                logger.error(
                    "web-tool-guard: text exceeds maximum chunk count (%d) — blocking",
                    MAX_CHUNK_COUNT,
                )
                raise RuntimeError(
                    f"Web tool result guard: result exceeds chunk limit "
                    f"({MAX_CHUNK_COUNT}) — request blocked"
                ) from exc
            logger.error(
                "web-tool-guard: tokenizer/chunking failed — blocking"
            )
            raise RuntimeError(
                "Web tool result guard: tokenizer or chunking failure — "
                "request blocked"
            ) from exc

    async def _classify_chunks(
        self, chunks: list[str], tool_name: str
    ) -> None:
        """Run Prompt Guard on each chunk. Block if ANY chunk is malicious.

        Fail closed on Prompt Guard error or timeout.
        """
        async def classify_all() -> None:
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

        try:
            await asyncio.wait_for(
                classify_all(), timeout=PROMPT_GUARD_TOTAL_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                "Web tool result guard: Prompt Guard total timeout — request blocked"
            ) from exc

        logger.debug(
            "web-tool-guard: all %d chunks classified as benign", len(chunks)
        )
