"""AiAlchemy guardrail configuration constants.

All thresholds, limits, and names are version-controlled here.
Changes require review before production activation.

Environment variable overrides use the same name as the constant
(e.g. PRESIDIO_TIMEOUT_SECONDS, PROMPT_GUARD_THRESHOLD).
"""

from __future__ import annotations

import os

# --- Guardrail names (match LiteLLM policy configuration) ---
GUARDRAIL_PII_INPUT_NAME: str = "aialchemy-pii-input-v1"
GUARDRAIL_WEB_TOOL_RESULT_NAME: str = "aialchemy-web-tool-result-v1"
GUARDRAIL_PII_OUTPUT_NAME: str = "aialchemy-pii-output-v1"

# --- Web-tool allowlist ---
# Every deployed Hermes tool that returns remote web content MUST be listed.
# Tool renames and new web tools MUST update this list and tests before activation.
WEB_TOOL_ALLOWLIST: frozenset[str] = frozenset(
    {
        "web_search",
        "web_extract",
        "browser_navigate",
        "browser_snapshot",
        "browser_evaluate",
        "browser_click",
    }
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
# Pinned HuggingFace model identifier for Prompt Guard 2 86M.
PROMPT_GUARD_MODEL: str = os.environ.get(
    "PROMPT_GUARD_MODEL", "meta-llama/Llama-Prompt-Guard-2-86M"
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

# --- Prompt Guard chunking ---
# Maximum TOKENS per chunk sent to Prompt Guard 2.
# The model's context window is 512 tokens — chunks must not exceed this.
PROMPT_GUARD_CHUNK_SIZE: int = int(
    os.environ.get("PROMPT_GUARD_CHUNK_SIZE", "512")
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

# --- Streaming output guard ---
# Sentence delimiters that indicate a complete segment for buffered inspection.
STREAM_BUFFER_SENTENCE_DELIMITERS: list[str] = [
    ".\n",
    "!\n",
    "?\n",
    ". ",
    "! ",
    "? ",
    "\n\n",
]
