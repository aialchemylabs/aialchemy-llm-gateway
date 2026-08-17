"""AiAlchemy PII Output Guard — inspects final user-visible text for PII.

Pipeline position: step 4 (aialchemy-pii-output-v1).
Runs after the trusted provider returns its final answer, before the user
receives it.

Presidio masks any remaining PII in the final response. On failure, no
partial text is released to the user.

Per spec §4.4: Prompt Guard MUST NOT inspect final provider answers.
This guard uses Presidio only.

Streaming: Per spec §4.4, "Streaming MUST release no uninspected final text.
The protected route MUST buffer and safely re-emit the inspected response,
or remain non-streaming until equivalent streaming behavior is proven."

This implementation operates on the post_call response text. For streaming
responses, LiteLLM must be configured to buffer the full response before
invoking the post_call guardrail, OR streaming must be disabled on the
protected route until a streaming-compatible implementation is proven.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Optional

from litellm.integrations.custom_guardrail import CustomGuardrail

from guardrails.config import GUARDRAIL_PII_OUTPUT_NAME
from guardrails.presidio_client import PresidioClient, PresidioError

logger = logging.getLogger(__name__)


class AiAlchemyPiiOutputGuard(CustomGuardrail):
    """Masks PII in final user-visible response text before release.

    This guard is STATELESS — each apply_guardrail call is independent.
    No per-request buffer state is maintained.
    """

    guardrail_name: str = GUARDRAIL_PII_OUTPUT_NAME
    event_hook: list[str] = ["post_call"]

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
        """Inspect final response text for PII and mask before user release.

        Only processes response-type inputs. Blocks entirely on failure —
        no partial text escapes.
        """
        if input_type != "response":
            return inputs

        logger.debug("pii-output-guard: inspecting final response text")

        texts = inputs.get("texts", [])
        if not texts:
            return inputs

        masked_texts: list[str] = []
        for text in texts:
            if not text:
                masked_texts.append(text)
                continue
            masked = await self._anonymize_text(text)
            masked_texts.append(masked)

        # Rewrite the texts in the inputs dict
        inputs["texts"] = masked_texts

        return inputs

    async def _anonymize_text(self, text: str) -> str:
        """Run Presidio on text. Fail closed — no partial release on error.

        Never logs the content or PII found.
        """
        try:
            return await self._presidio.analyze_and_anonymize(text)
        except PresidioError as exc:
            logger.error(
                "pii-output-guard: Presidio failure — blocking response release"
            )
            raise RuntimeError(
                "PII output guard: Presidio unavailable — "
                "response blocked, no partial text released"
            ) from exc
        except Exception as exc:
            logger.error(
                "pii-output-guard: unexpected error — blocking response release"
            )
            raise RuntimeError(
                "PII output guard: unexpected failure — "
                "response blocked, no partial text released"
            ) from exc
