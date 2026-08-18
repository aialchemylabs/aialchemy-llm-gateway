"""Shared mock setup for guardrail tests.

Must be imported BEFORE any guardrails module so that `litellm` is stubbed out —
litellm is only installed inside the Docker image, not on a developer host.

The stub base class mirrors just enough of litellm's CustomGuardrail for the
guards to subclass and instantiate: it accepts arbitrary kwargs and does not
require any litellm machinery.
"""
import sys
from types import ModuleType


class CustomGuardrail:
    """Minimal stand-in for litellm.integrations.custom_guardrail.CustomGuardrail."""

    def __init__(self, **kwargs):
        # Real CustomGuardrail stores config kwargs; tests do not depend on them.
        for key, value in kwargs.items():
            setattr(self, key, value)


class GuardrailRaisedException(Exception):
    """Minimal stand-in carrying LiteLLM's stable HTTP status contract."""

    def __init__(
        self,
        guardrail_name=None,
        message="",
        should_wrap_with_default_message=True,
        status_code=400,
    ):
        self.guardrail_name = guardrail_name
        self.status_code = status_code
        self.message = message
        super().__init__(message)


def _install_litellm_stub() -> None:
    """Register fake litellm modules in sys.modules (idempotent)."""
    if "litellm.integrations.custom_guardrail" in sys.modules:
        return

    litellm = ModuleType("litellm")
    integrations = ModuleType("litellm.integrations")
    custom_guardrail = ModuleType("litellm.integrations.custom_guardrail")
    exceptions = ModuleType("litellm.exceptions")

    custom_guardrail.CustomGuardrail = CustomGuardrail
    exceptions.GuardrailRaisedException = GuardrailRaisedException
    integrations.custom_guardrail = custom_guardrail
    litellm.integrations = integrations

    sys.modules["litellm"] = litellm
    sys.modules["litellm.integrations"] = integrations
    sys.modules["litellm.integrations.custom_guardrail"] = custom_guardrail
    sys.modules["litellm.exceptions"] = exceptions


_install_litellm_stub()
