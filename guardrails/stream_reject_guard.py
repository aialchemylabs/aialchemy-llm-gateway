"""AiAlchemy Stream Reject Guard — blocks streaming on protected routes.

Pipeline position: pre_call (earliest reject possible).
Raises a stable HTTP-400 guardrail exception immediately if stream=True,
rather than silently coercing or fake-streaming the response.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Optional

from litellm.exceptions import GuardrailRaisedException
from litellm.integrations.custom_guardrail import CustomGuardrail

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
        if request_data.get("stream") is True:
            raise GuardrailRaisedException(
                guardrail_name=self.guardrail_name,
                message=(
                    "Streaming is not supported on the protected route. "
                    "Set stream: false."
                ),
                should_wrap_with_default_message=False,
                status_code=400,
            )

        return inputs
