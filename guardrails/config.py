"""AiAlchemy guardrail configuration constants.

All thresholds, limits, and names are version-controlled here.
Changes require review before production activation.

Environment variable overrides use the same name as the constant
(e.g. PRESIDIO_TIMEOUT_SECONDS, PROMPT_GUARD_THRESHOLD).
"""

from __future__ import annotations

import math
import os
import re

# --- Guardrail names (match LiteLLM policy configuration) ---
GUARDRAIL_PII_INPUT_NAME: str = "aialchemy-pii-input-v1"
GUARDRAIL_WEB_TOOL_RESULT_NAME: str = "aialchemy-web-tool-result-v1"
GUARDRAIL_PII_OUTPUT_NAME: str = "aialchemy-pii-output-v1"

# --- Web-tool allowlist ---
# Core Infra supplies the authoritative deployed-tool manifest. The image keeps
# the approved baseline as a safe local/test default, but malformed deployment
# wiring fails startup instead of silently falling back to a broader list.
_DEFAULT_WEB_TOOL_ALLOWLIST: tuple[str, ...] = (
    "web_search",
    "web_extract",
    "browser_console",
    "browser_get_images",
    "browser_vision",
    "browser_snapshot",
    "browser_navigate",
)
_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _parse_web_tool_allowlist(raw_value: str) -> frozenset[str]:
    entries = [entry.strip() for entry in raw_value.split(",")]
    if not entries or any(not entry for entry in entries):
        raise ValueError("WEB_TOOL_ALLOWLIST must contain at least one tool name")
    if any(_TOOL_NAME_PATTERN.fullmatch(entry) is None for entry in entries):
        raise ValueError("WEB_TOOL_ALLOWLIST contains an invalid tool name")
    if len(entries) != len(set(entries)):
        raise ValueError("WEB_TOOL_ALLOWLIST contains duplicate tool names")
    return frozenset(entries)


WEB_TOOL_ALLOWLIST: frozenset[str] = _parse_web_tool_allowlist(
    os.environ.get("WEB_TOOL_ALLOWLIST", ",".join(_DEFAULT_WEB_TOOL_ALLOWLIST))
)

# --- Presidio service endpoints ---
PRESIDIO_ANALYZER_URL: str = os.environ.get(
    "PRESIDIO_ANALYZER_URL", "http://presidio-analyzer:3000"
)
PRESIDIO_ANONYMIZER_URL: str = os.environ.get(
    "PRESIDIO_ANONYMIZER_URL", "http://presidio-anonymizer:3000"
)

# Timeout in seconds for Presidio analyze + anonymize calls.
PRESIDIO_TIMEOUT_SECONDS: float = float(
    os.environ.get("PRESIDIO_TIMEOUT_SECONDS", "5.0")
)
# Alias for internal use
PRESIDIO_TIMEOUT_SECS: float = PRESIDIO_TIMEOUT_SECONDS

# Presidio entity types to detect (per spec §6 entity manifest).
PRESIDIO_ENTITIES: list[str] = [
    # Australia
    "AU_TFN",
    "AU_MEDICARE",
    "AU_PASSPORT",
    # India
    "IN_AADHAAR",
    "IN_PAN",
    "IN_PASSPORT",
    "IN_VOTER",
    "IN_VEHICLE_REGISTRATION",
    # General identity and contact
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "LOCATION",
    # General financial
    "CREDIT_CARD",
    "IBAN_CODE",
]

# Language for Presidio analyzer.
PRESIDIO_LANGUAGE: str = "en"

# --- Prompt Guard model ---
# Pinned HuggingFace model identifier and immutable repository commit for
# Prompt Guard 2 86M.  Do not make either client- or environment-selectable:
# changing model artifacts is a reviewed source change, not runtime tuning.
PROMPT_GUARD_MODEL: str = "meta-llama/Llama-Prompt-Guard-2-86M"
PROMPT_GUARD_MODEL_REVISION: str = (
    "a8ded8e697ce7c355e395a0df51f94adb4a2fd27"
)
# Alias used by prompt_guard.py
PROMPT_GUARD_MODEL_ID: str = PROMPT_GUARD_MODEL

# Torch device for inference. CPU is the default for in-process loading.
PROMPT_GUARD_DEVICE: str = os.environ.get("PROMPT_GUARD_DEVICE", "cpu")

# Score threshold above which a non-BENIGN result is considered malicious.
PROMPT_GUARD_THRESHOLD: float = float(
    os.environ.get("PROMPT_GUARD_THRESHOLD", "0.85")
)
# Alias used by prompt_guard_client.py
PROMPT_GUARD_MALICIOUS_THRESHOLD: float = PROMPT_GUARD_THRESHOLD

# Timeout in seconds for a single Prompt Guard classification call.
PROMPT_GUARD_TIMEOUT_SECONDS: float = float(
    os.environ.get("PROMPT_GUARD_TIMEOUT_SECONDS", "10.0")
)
# Alias for internal use
PROMPT_GUARD_TIMEOUT_SECS: float = PROMPT_GUARD_TIMEOUT_SECONDS

# Timeout in seconds for initial model load (download + warm-up).
PROMPT_GUARD_INIT_TIMEOUT_SECS: float = float(
    os.environ.get("PROMPT_GUARD_INIT_TIMEOUT_SECS", "60.0")
)

# --- Prompt Guard chunking ---
# Maximum content tokens per chunk sent to Prompt Guard 2.  The model's total
# context is 512; two positions are reserved for classifier special tokens.
PROMPT_GUARD_CHUNK_SIZE: int = int(
    os.environ.get("PROMPT_GUARD_CHUNK_SIZE", "510")
)

# Overlap in tokens between adjacent chunks to avoid splitting injections.
PROMPT_GUARD_CHUNK_OVERLAP: int = int(
    os.environ.get("PROMPT_GUARD_CHUNK_OVERLAP", "64")
)

# Maximum number of chunks before the guard fails closed (size limit).
PROMPT_GUARD_MAX_CHUNKS: int = int(
    os.environ.get("PROMPT_GUARD_MAX_CHUNKS", "64")
)
# Alias for internal use
MAX_CHUNK_COUNT: int = PROMPT_GUARD_MAX_CHUNKS

# Raw UTF-8 result limit, enforced before Presidio or tokenization.  This is
# distinct from the token/chunk limit and prevents pathological Unicode or
# tokenizer-expansion inputs from consuming unbounded memory/CPU.
PROMPT_GUARD_MAX_RESULT_BYTES: int = int(
    os.environ.get("PROMPT_GUARD_MAX_RESULT_BYTES", "262144")
)

# Structured output parts are bounded separately from bytes. Without this, an
# attacker can submit an enormous list of empty strings that consumes CPU and
# memory while remaining a zero-byte Prompt Guard result.
MAX_TOOL_OUTPUT_PARTS: int = int(
    os.environ.get("MAX_TOOL_OUTPUT_PARTS", "1024")
)

# Total wall-clock budget for classifying all chunks in one web-tool result.
PROMPT_GUARD_TOTAL_TIMEOUT_SECONDS: float = float(
    os.environ.get("PROMPT_GUARD_TOTAL_TIMEOUT_SECONDS", "30.0")
)

# Streaming rejection is intentionally not feature-flagged.  The protected
# route may omit this guard during pre-activation configuration work, but once
# attached a runtime setting must not weaken it before inspected streaming is
# implemented and proven.


def _require_finite_positive(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and greater than zero")


def _validate_configuration() -> None:
    """Reject unsafe runtime overrides before the proxy can accept traffic."""

    _require_finite_positive("PRESIDIO_TIMEOUT_SECONDS", PRESIDIO_TIMEOUT_SECONDS)
    if (
        not math.isfinite(PROMPT_GUARD_THRESHOLD)
        or PROMPT_GUARD_THRESHOLD < 0.0
        or PROMPT_GUARD_THRESHOLD > 1.0
    ):
        raise ValueError("PROMPT_GUARD_THRESHOLD must be finite and within [0, 1]")
    _require_finite_positive(
        "PROMPT_GUARD_TIMEOUT_SECONDS", PROMPT_GUARD_TIMEOUT_SECONDS
    )
    _require_finite_positive(
        "PROMPT_GUARD_INIT_TIMEOUT_SECS", PROMPT_GUARD_INIT_TIMEOUT_SECS
    )
    _require_finite_positive(
        "PROMPT_GUARD_TOTAL_TIMEOUT_SECONDS", PROMPT_GUARD_TOTAL_TIMEOUT_SECONDS
    )

    if not 1 <= PROMPT_GUARD_CHUNK_SIZE <= 510:
        raise ValueError("PROMPT_GUARD_CHUNK_SIZE must be within [1, 510]")
    if not 0 <= PROMPT_GUARD_CHUNK_OVERLAP < PROMPT_GUARD_CHUNK_SIZE:
        raise ValueError(
            "PROMPT_GUARD_CHUNK_OVERLAP must be non-negative and smaller "
            "than PROMPT_GUARD_CHUNK_SIZE"
        )
    for name, value in (
        ("PROMPT_GUARD_MAX_CHUNKS", PROMPT_GUARD_MAX_CHUNKS),
        ("PROMPT_GUARD_MAX_RESULT_BYTES", PROMPT_GUARD_MAX_RESULT_BYTES),
        ("MAX_TOOL_OUTPUT_PARTS", MAX_TOOL_OUTPUT_PARTS),
    ):
        if value <= 0:
            raise ValueError(f"{name} must be greater than zero")


_validate_configuration()
