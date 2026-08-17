"""AiAlchemy Stream Reject Guard — blocks streaming on protected routes.

Pipeline position: pre_call (earliest reject possible).
Raises RuntimeError immediately if stream=True, giving the caller a clear
error rather than silently coercing or fake-streaming the response.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Optional

from litellm.integrations.custom_guardrail import CustomGuardrail

from guardrails.config import STREAM_REJECTION_ENABLED

logger = logging.getLogger(__name__)


class AiAlchemyStreamRejectGuard(CustomGuardrail):
    """Rejects streaming requests on the protected route."""

    guardrail_name: str = "aialchemy-stream-reject-v1"
    event_hook: list[str] = ["pre_call"]

    async def apply_guardrail(
        self,
        inputs: dict[str, Any],
        request_data: dict[str, Any],
        input_type: Literal["request", "response"],
        logging_obj: Optional[Any] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Reject if stream is True; pass through otherwise."""
        if not STREAM_REJECTION_ENABLED:
            return inputs

        if request_data.get("stream") is True:
            raise RuntimeError(
                "Streaming is not supported on the protected route. "
                "Set stream: false."
            )

        return inputs
