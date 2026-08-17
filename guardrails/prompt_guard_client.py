"""Prompt Guard 2 client for injection/jailbreak classification.

Loads meta-llama/Llama-Prompt-Guard-2-86M in-process via transformers.
The model is an encoder-only mDeBERTa-v3 binary classifier (86M params) —
fast inference on CPU without a separate service.

Prompt Guard 2 uses BINARY classification: "benign" or "malicious".
Context window is 512 tokens. Inputs longer than this must be chunked
externally before calling classify().

Classifier input content is NEVER logged.

SECURITY POSTURE: Fail closed. Any unexpected condition (bad label, invalid
score, empty result, timeout, load failure) raises PromptGuardError — callers
MUST treat raised errors as blocked requests.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import threading
from typing import Any

from guardrails.config import (
    PROMPT_GUARD_INIT_TIMEOUT_SECS,
    PROMPT_GUARD_MALICIOUS_THRESHOLD,
    PROMPT_GUARD_TIMEOUT_SECS,
)

logger = logging.getLogger(__name__)

_VALID_LABELS = frozenset({"benign", "malicious"})


class PromptGuardError(Exception):
    """Raised on any Prompt Guard failure (model load, inference, timeout, validation).

    Callers MUST treat this as a block — fail closed.
    """

    pass


class PromptGuardClient:
    """In-process Prompt Guard 2 86M binary classifier.

    Lazy-loads the model on first classification call. Thread-safe via a
    threading lock on initialization and the transformers pipeline for inference.

    Labels: "benign" or "malicious" (Prompt Guard 2 is binary).
    Context: 512 tokens max per call — callers must chunk longer inputs.

    Security: Fails closed on all unexpected conditions.
    """

    # Pinned model identifier — exact revision must be integrity-checked
    # in production deployment.
    MODEL_ID: str = os.environ.get(
        "PROMPT_GUARD_MODEL_ID", "meta-llama/Llama-Prompt-Guard-2-86M"
    )

    def __init__(self) -> None:
        self._pipeline: Any = None
        self._threshold: float = PROMPT_GUARD_MALICIOUS_THRESHOLD
        self._timeout: float = PROMPT_GUARD_TIMEOUT_SECS
        self._init_timeout: float = PROMPT_GUARD_INIT_TIMEOUT_SECS
        self._lock: threading.Lock = threading.Lock()

    def _load_model(self) -> None:
        """Lazy-load the Prompt Guard model pipeline with bounded init time.

        Thread-safe: only one thread loads at a time. Raises PromptGuardError
        if loading exceeds PROMPT_GUARD_INIT_TIMEOUT_SECS or fails for any reason.
        """
        if self._pipeline is not None:
            return

        with self._lock:
            # Double-check after acquiring lock
            if self._pipeline is not None:
                return

            import concurrent.futures

            try:
                from transformers import pipeline as hf_pipeline
            except ImportError as exc:
                raise PromptGuardError(
                    "transformers library not available"
                ) from exc

            def _do_load() -> Any:
                return hf_pipeline(
                    "text-classification",
                    model=self.MODEL_ID,
                    device=-1,  # CPU only
                    truncation=True,
                    max_length=512,  # Model context window
                )

            logger.info("prompt-guard-client: loading model %s", self.MODEL_ID)

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_do_load)
                try:
                    pipeline = future.result(timeout=self._init_timeout)
                except concurrent.futures.TimeoutError as exc:
                    raise PromptGuardError(
                        f"Prompt Guard model loading timed out after {self._init_timeout}s"
                    ) from exc
                except Exception as exc:
                    raise PromptGuardError(
                        f"Failed to load Prompt Guard model: {exc}"
                    ) from exc

            self._pipeline = pipeline
            logger.info("prompt-guard-client: model loaded successfully")

    @classmethod
    def ensure_ready(cls) -> "PromptGuardClient":
        """Create a client and eagerly load the model at startup.

        Use during application init to surface load failures early.
        Raises PromptGuardError if the model cannot be loaded within the
        configured init timeout.
        """
        client = cls()
        client._load_model()
        return client

    def _validate_result(self, result: Any) -> tuple[str, float]:
        """Validate model output strictly. Fail closed on any anomaly.

        Returns (label, score) only if both are valid.
        Raises PromptGuardError otherwise.
        """
        # Empty/missing result
        if not result:
            raise PromptGuardError(
                "Prompt Guard returned empty/missing result — failing closed"
            )

        classification = result[0] if isinstance(result, list) else result

        if not isinstance(classification, dict):
            raise PromptGuardError(
                f"Prompt Guard returned unexpected result type: {type(classification).__name__}"
            )

        # --- Label validation ---
        raw_label = classification.get("label")
        if raw_label is None:
            raise PromptGuardError(
                "Prompt Guard result missing 'label' field — failing closed"
            )

        if not isinstance(raw_label, str):
            raise PromptGuardError(
                f"Prompt Guard label is not a string: {type(raw_label).__name__} — failing closed"
            )

        label = raw_label.strip().lower()
        if label not in _VALID_LABELS:
            raise PromptGuardError(
                f"Prompt Guard returned unexpected label '{raw_label}' — failing closed"
            )

        # --- Score validation ---
        raw_score = classification.get("score")
        if raw_score is None:
            raise PromptGuardError(
                "Prompt Guard result missing 'score' field — failing closed"
            )

        try:
            score = float(raw_score)
        except (TypeError, ValueError) as exc:
            raise PromptGuardError(
                f"Prompt Guard score is not numeric: {raw_score!r} — failing closed"
            ) from exc

        if not math.isfinite(score):
            raise PromptGuardError(
                f"Prompt Guard score is not finite: {score} — failing closed"
            )

        if score < 0.0 or score > 1.0:
            raise PromptGuardError(
                f"Prompt Guard score {score} outside [0.0, 1.0] — failing closed"
            )

        return label, score

    async def classify(self, text: str) -> bool:
        """Classify a text chunk as benign or malicious.

        Returns True if the text is classified as "malicious" with score
        at or above the configured threshold.

        Returns False ONLY when:
        - label is "benign", OR
        - label is "malicious" but score < threshold

        Raises PromptGuardError on ANY unexpected condition (empty input,
        model failure, timeout, invalid label/score). Callers MUST treat
        raised errors as blocked.

        The input text MUST be pre-chunked to fit within 512 tokens.
        Never logs the classified content.
        """
        # Empty/missing input: fail closed
        if not text or not text.strip():
            raise PromptGuardError(
                "Prompt Guard received empty/blank input — failing closed"
            )

        # Ensure model is loaded (thread-safe, bounded)
        if self._pipeline is None:
            self._load_model()

        try:
            loop = asyncio.get_running_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(None, self._pipeline, text),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError as exc:
            raise PromptGuardError(
                f"Prompt Guard inference timed out after {self._timeout}s"
            ) from exc
        except PromptGuardError:
            raise
        except Exception as exc:
            raise PromptGuardError(
                f"Prompt Guard inference error: {type(exc).__name__} — failing closed"
            ) from exc

        # Strict validation — raises on anything unexpected
        label, score = self._validate_result(result)

        # Classification logic:
        # malicious AND score >= threshold -> True (block)
        # malicious AND score < threshold  -> False (below confidence)
        # benign                           -> False
        is_malicious = label == "malicious" and score >= self._threshold

        if is_malicious:
            logger.debug(
                "prompt-guard-client: classified as malicious (score above threshold)",
            )
        else:
            logger.debug("prompt-guard-client: classified as benign")

        return is_malicious
