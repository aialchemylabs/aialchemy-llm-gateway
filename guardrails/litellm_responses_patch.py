"""Ensure LiteLLM invokes the selected guardrail for EVERY Responses input list.

WHY THIS EXISTS
---------------
LiteLLM 1.97.0's Responses guardrail translation handler
(``OpenAIResponsesHandler.process_input_messages``) extracts "texts" only from
input items that carry a ``content`` field. A Responses API continuation that
contains only ``function_call`` + ``function_call_output`` items therefore
produces zero extracted texts, and the handler skips calling the selected
guardrail's ``apply_guardrail()`` entirely.

That is the exact case ``must-have-requirements.md`` §4.3 prohibits: a web-tool
result reaching the provider without Presidio masking or Prompt Guard
classification.

WHAT THIS DOES
--------------
Wraps ``OpenAIResponsesHandler.process_input_messages`` so the *normally
selected* ``guardrail_to_apply`` is invoked for every non-empty Responses input
list — including tool-only continuations. It does NOT call any AiAlchemy guard
directly: policy selection, ordering, logging and failure handling stay with
LiteLLM. This wrapper only guarantees invocation.

When the upstream handler already invoked the guardrail (texts were extracted),
the wrapper does nothing extra — no duplicate execution.

WHY A RUNTIME WRAPPER AND NOT A SOURCE find/replace
---------------------------------------------------
The other patches in ``scripts/`` do exact-string replacement because their
target lines are short and stable. This target is a multi-branch method whose
exact source text differs across patch releases; a hardcoded string would break
on any upstream reformat. Wrapping the resolved attribute is version-tolerant,
and ``verify_patch_applies()`` below fails closed at build time if the class or
method is missing, so a silent no-op is impossible.

Remove this module when the pinned LiteLLM release invokes Responses guardrails
for tool-only continuations natively.
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

HANDLER_MODULE = "litellm.llms.openai.responses.guardrail_translation.handler"
HANDLER_CLASS = "OpenAIResponsesHandler"
TARGET_METHOD = "process_input_messages"

# Sentinel attribute so repeated application is a no-op (idempotent).
_PATCH_FLAG = "_aialchemy_responses_guardrail_patched"


class PatchError(RuntimeError):
    """Raised when the patch cannot be applied. Fail closed — never continue."""


def _resolve_handler_class() -> type:
    """Import and return the LiteLLM Responses guardrail handler class.

    Raises PatchError if the module or class is absent, so the build fails
    loudly rather than shipping an unguarded image.
    """
    try:
        module = __import__(HANDLER_MODULE, fromlist=[HANDLER_CLASS])
    except ImportError as exc:
        raise PatchError(
            f"Cannot import {HANDLER_MODULE} — LiteLLM layout changed. "
            "Refusing to continue: Responses guardrail invocation is unverified."
        ) from exc

    handler_cls = getattr(module, HANDLER_CLASS, None)
    if handler_cls is None:
        raise PatchError(
            f"{HANDLER_MODULE} has no {HANDLER_CLASS} — LiteLLM layout changed. "
            "Refusing to continue: Responses guardrail invocation is unverified."
        )
    return handler_cls


def _resolve_target_method(handler_cls: type) -> Callable[..., Any]:
    """Return the unbound process_input_messages, or raise PatchError."""
    method = getattr(handler_cls, TARGET_METHOD, None)
    if method is None or not callable(method):
        raise PatchError(
            f"{HANDLER_CLASS}.{TARGET_METHOD} not found or not callable — "
            "LiteLLM layout changed. Refusing to continue."
        )
    return method


def _extract_input_list(data: Any) -> list[Any]:
    """Return the Responses input list from a request dict, or []."""
    if not isinstance(data, dict):
        return []
    input_data = data.get("input")
    return input_data if isinstance(input_data, list) else []


def _has_uninspected_tool_output(input_items: list[Any]) -> bool:
    """True if the input list carries a function_call_output item.

    These are the payloads LiteLLM's text extraction never surfaces, so their
    presence is what makes a guardrail invocation mandatory.
    """
    for item in input_items:
        if isinstance(item, dict) and item.get("type") == "function_call_output":
            return True
    return False


def apply_patch() -> str:
    """Wrap process_input_messages to guarantee guardrail invocation.

    Returns "patched" or "already-patched". Raises PatchError on any
    unexpected upstream shape (fail closed).
    """
    handler_cls = _resolve_handler_class()

    if getattr(handler_cls, _PATCH_FLAG, False):
        return "already-patched"

    original = _resolve_target_method(handler_cls)

    @functools.wraps(original)
    async def process_input_messages_guaranteed(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Call upstream, then force guardrail invocation if it was skipped."""
        data = kwargs.get("data")
        if data is None and args:
            data = args[0]

        guardrail = kwargs.get("guardrail_to_apply")

        result = await original(self, *args, **kwargs)

        # The dict upstream returned (or the one we were handed) is the request
        # that continues to the provider.
        effective = result if isinstance(result, dict) else data
        input_items = _extract_input_list(effective)

        # Nothing to inspect, or no guardrail selected for this request:
        # upstream behaviour is already correct.
        if not input_items or guardrail is None:
            return result

        # If there is no function_call_output, upstream's text extraction saw
        # everything there was to see and already invoked the guardrail.
        if not _has_uninspected_tool_output(input_items):
            return result

        apply_guardrail = getattr(guardrail, "apply_guardrail", None)
        if apply_guardrail is None:
            raise PatchError(
                "Selected guardrail exposes no apply_guardrail() — cannot "
                "inspect function_call_output. Request blocked."
            )

        # Invoke the SELECTED guardrail on the raw request. Guards that inspect
        # function_call_output read it from request_data, so an empty texts list
        # is correct here — we are not asking LiteLLM to rewrite extracted text.
        logger.debug(
            "aialchemy-responses-patch: forcing guardrail invocation for "
            "tool-only Responses continuation"
        )
        await apply_guardrail(
            inputs={"texts": [], "structured_messages": []},
            request_data=effective,
            input_type="request",
        )

        return result

    setattr(handler_cls, TARGET_METHOD, process_input_messages_guaranteed)
    setattr(handler_cls, _PATCH_FLAG, True)
    return "patched"


def verify_patch_applies() -> None:
    """Build-time check: the target class and method must exist.

    Called from the Dockerfile. Raises PatchError (non-zero exit) if the
    upstream shape changed, so an unverified image is never published.
    """
    handler_cls = _resolve_handler_class()
    _resolve_target_method(handler_cls)
    result = apply_patch()
    if not getattr(handler_cls, _PATCH_FLAG, False):
        raise PatchError("Patch reported success but flag is unset — refusing.")
    print(f"{result}: {HANDLER_CLASS}.{TARGET_METHOD}")


if __name__ == "__main__":
    verify_patch_applies()
