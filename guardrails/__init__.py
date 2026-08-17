"""AiAlchemy LLM Gateway guardrails package.

Provides LiteLLM custom guardrails for the aialchemy-global-baseline-v1 policy:
- AiAlchemyPiiInputGuard: masks PII in provider-bound content
- AiAlchemyWebToolResultGuard: inspects untrusted web-tool results
- AiAlchemyPiiOutputGuard: masks PII in final user-visible responses

Guards are imported lazily — litellm is only available inside the Docker image,
not on the host during local test runs.
"""

import importlib as _importlib

__all__ = [
    "AiAlchemyPiiInputGuard",
    "AiAlchemyPiiOutputGuard",
    "AiAlchemyStreamRejectGuard",
    "AiAlchemyWebToolResultGuard",
]

# Guard class names that require litellm — lazy import only.
_LAZY_GUARD_CLASSES = {
    "AiAlchemyPiiInputGuard": "guardrails.pii_input_guard",
    "AiAlchemyPiiOutputGuard": "guardrails.pii_output_guard",
    "AiAlchemyStreamRejectGuard": "guardrails.stream_reject_guard",
    "AiAlchemyWebToolResultGuard": "guardrails.web_tool_result_guard",
}


def __getattr__(name: str):  # noqa: ANN204
    """Lazy import guard classes and allow submodule attribute access."""
    if name in _LAZY_GUARD_CLASSES:
        module = _importlib.import_module(_LAZY_GUARD_CLASSES[name])
        return getattr(module, name)
    # Allow submodule access (e.g. guardrails.presidio_client)
    try:
        return _importlib.import_module(f"guardrails.{name}")
    except ImportError:
        raise AttributeError(f"module 'guardrails' has no attribute {name!r}")
