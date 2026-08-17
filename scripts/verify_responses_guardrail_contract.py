#!/usr/bin/env python3
"""Prove the Responses guardrail wrapper can actually receive its inputs.

WHY THIS EXISTS
---------------
``guardrails/litellm_responses_patch.py`` wraps
``OpenAIResponsesHandler.process_input_messages`` and reads two things out of
the call: the request dict and the guardrail LiteLLM selected for this request.
It reads them defensively (kwargs first, then positional), because the upstream
signature is not part of LiteLLM's public API.

That defensiveness hides a dangerous failure mode: if upstream renames the
guardrail parameter, the wrapper resolves it to ``None``, returns early, and
every web-tool result flows to the provider uninspected — with the whole test
suite still green, because the unit tests call ``apply_guardrail`` directly and
never exercise the wrapper's argument extraction.

This check closes that gap. It asserts, against the installed LiteLLM, that the
parameter names the wrapper depends on are really there. If upstream changes
them, the build fails and prints the actual signature so the wrapper can be
corrected — instead of silently shipping an unguarded image.

Run as a build step. Non-zero exit fails the build.
"""

from __future__ import annotations

import inspect
import sys

HANDLER_MODULE = "litellm.llms.openai.responses.guardrail_translation.handler"
HANDLER_CLASS = "OpenAIResponsesHandler"
TARGET_METHOD = "process_input_messages"

# Parameter names guardrails/litellm_responses_patch.py reads by keyword.
# Keep in sync with that module's argument extraction.
REQUIRED_PARAMS = ("data", "guardrail_to_apply")


class ContractError(RuntimeError):
    """Raised when the upstream signature no longer matches our assumptions."""


def resolve_original_method():
    """Return the unwrapped process_input_messages function."""
    try:
        module = __import__(HANDLER_MODULE, fromlist=[HANDLER_CLASS])
    except ImportError as exc:
        raise ContractError(
            f"Cannot import {HANDLER_MODULE}: {exc}. "
            "LiteLLM layout changed — the Responses guardrail wrapper is unverified."
        ) from exc

    handler_cls = getattr(module, HANDLER_CLASS, None)
    if handler_cls is None:
        raise ContractError(
            f"{HANDLER_MODULE} has no {HANDLER_CLASS} — layout changed."
        )

    method = getattr(handler_cls, TARGET_METHOD, None)
    if method is None:
        raise ContractError(
            f"{HANDLER_CLASS}.{TARGET_METHOD} is missing — layout changed."
        )

    # sitecustomize may already have wrapped it; inspect the real function.
    return inspect.unwrap(method)


def main() -> None:
    original = resolve_original_method()
    signature = inspect.signature(original)
    params = signature.parameters

    print(f"{HANDLER_CLASS}.{TARGET_METHOD}{signature}")

    if not inspect.iscoroutinefunction(original):
        raise ContractError(
            f"{TARGET_METHOD} is not a coroutine function. The wrapper awaits it; "
            "a sync upstream implementation would break at runtime."
        )

    missing = [name for name in REQUIRED_PARAMS if name not in params]
    if missing:
        raise ContractError(
            f"{TARGET_METHOD} does not accept {missing}. The Responses guardrail "
            "wrapper reads these by keyword; without them it would resolve the "
            "guardrail to None and silently skip inspection of "
            "function_call_output payloads.\n"
            f"  Actual parameters: {list(params)}\n"
            "Fix guardrails/litellm_responses_patch.py to read the real names, "
            "and update REQUIRED_PARAMS here."
        )

    print(
        "responses-guardrail-contract: OK — wrapper can receive "
        f"{', '.join(REQUIRED_PARAMS)}"
    )


if __name__ == "__main__":
    try:
        main()
    except ContractError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
