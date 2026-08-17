"""AiAlchemy PII Input Guard — masks PII in provider-bound content.

Pipeline position: step 1 (aialchemy-pii-input-v1).
Runs before Prompt Guard and before the trusted provider sees any content.
Masks configured PII entities using Presidio with non-reversible typed
replacement (e.g. <EMAIL_ADDRESS>, <PERSON>).

Works through LiteLLM's inputs['texts'] return path:
- Transforms inputs['texts'] in-place (same list order, same cardinality)
- Mutates request_data['instructions'] directly (not extracted by LiteLLM)
- Mutates function_call_output.output in request_data['input'] directly
  (LiteLLM doesn't extract it)

Fail-closed: any Presidio error or timeout blocks the request entirely.
PII content is NEVER logged.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Optional

from litellm.integrations.custom_guardrail import CustomGuardrail

from guardrails.presidio_client import PresidioClient, PresidioError

logger = logging.getLogger(__name__)


class AiAlchemyPiiInputGuard(CustomGuardrail):
    """Masks PII in all provider-bound text before it leaves LiteLLM.

    Operates on inputs['texts'] — the list LiteLLM extracted from the request.
    Returns inputs with texts transformed (same order, same length).
    Also mutates request_data fields that LiteLLM does not extract.
    """

    guardrail_name: str = "aialchemy-pii-input-v1"
    event_hook: list[str] = ["pre_call"]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._presidio = PresidioClient()

    async def apply_guardrail(
        self,
        inputs: dict[str, Any],
        request_data: dict[str, Any],
        input_type: Literal["request", "response"] = "request",
        logging_obj: Optional[Any] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Mask PII in extracted texts and raw request fields.

        Strategy:
        1. Transform inputs['texts'] — LiteLLM writes these back to the request.
        2. Mutate request_data['instructions'] — not extracted by LiteLLM.
        3. Mutate function_call_output.output in request_data['input'] — not extracted.
        4. Return inputs (with texts replaced, same cardinality).
        """
        if input_type != "request":
            return inputs

        logger.debug("pii-input-guard: scanning provider-bound content")

        texts: list[str] = inputs.get("texts", [])

        # --- 1. Transform inputs['texts'] (LiteLLM-extracted content) ---
        if texts:
            masked_texts = await self._mask_texts(texts)

            # Fail-closed cardinality check
            if len(masked_texts) != len(texts):
                raise RuntimeError(
                    "PII input guard: cardinality mismatch after masking "
                    f"(expected {len(texts)}, got {len(masked_texts)}) — request blocked"
                )

            inputs["texts"] = masked_texts

        # --- 2. Mutate instructions (raw request_data, not extracted by LiteLLM) ---
        if "instructions" in request_data and isinstance(
            request_data["instructions"], str
        ):
            request_data["instructions"] = await self._anonymize_text(
                request_data["instructions"]
            )

        # --- 3. Mutate function_call_output.output (not extracted by LiteLLM) ---
        if "input" in request_data and isinstance(request_data["input"], list):
            await self._mask_function_call_outputs(request_data["input"])

        return inputs

    async def _mask_texts(self, texts: list[str]) -> list[str]:
        """Mask PII in each text, preserving list order and length."""
        masked: list[str] = []
        for text in texts:
            masked.append(await self._anonymize_text(text))
        return masked

    async def _mask_function_call_outputs(
        self, input_items: list[dict[str, Any]]
    ) -> None:
        """Mask PII in function_call_output items within request_data['input'].

        These are not extracted by LiteLLM into inputs['texts'], so we mutate
        the request_data directly.
        """
        for item in input_items:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "function_call_output":
                continue
            output = item.get("output")
            if isinstance(output, str) and output.strip():
                item["output"] = await self._anonymize_text(output)

    async def _anonymize_text(self, text: str) -> str:
        """Run Presidio analysis and anonymization on a single text.

        Returns the original text unchanged if it's empty/whitespace.
        Raises RuntimeError on any Presidio failure (fail-closed).
        Never logs content.
        """
        if not text or not text.strip():
            return text

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
