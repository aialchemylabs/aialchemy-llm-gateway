"""Prompt Guard 2 client for injection/jailbreak classification.

Loads meta-llama/Llama-Prompt-Guard-2-86M in-process via transformers.
The model is an encoder-only mDeBERTa-v3 binary classifier (86M params) —
fast inference on CPU without a separate service.

Prompt Guard 2 uses BINARY classification: "benign" or "malicious".
Context window is 512 tokens. Inputs longer than this must be chunked
externally before calling classify().

Classifier input content is NEVER logged.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from guardrails.config import PROMPT_GUARD_MALICIOUS_THRESHOLD, PROMPT_GUARD_TIMEOUT_SECS

logger = logging.getLogger(__name__)


class PromptGuardError(Exception):
    """Raised on any Prompt Guard failure (model load, inference, timeout)."""

    pass


class PromptGuardClient:
    """In-process Prompt Guard 2 86M binary classifier.

    Lazy-loads the model on first classification call. Thread-safe via
    the transformers pipeline.

    Labels: "benign" or "malicious" (Prompt Guard 2 is binary).
    Context: 512 tokens max per call — callers must chunk longer inputs.
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

    def _load_model(self) -> None:
        """Lazy-load the Prompt Guard model pipeline."""
        try:
            from transformers import pipeline as hf_pipeline

            logger.info("prompt-guard-client: loading model %s", self.MODEL_ID)
            self._pipeline = hf_pipeline(
                "text-classification",
                model=self.MODEL_ID,
                device=-1,  # CPU only
                truncation=True,
                max_length=512,  # Model context window
            )
            logger.info("prompt-guard-client: model loaded successfully")
        except Exception as exc:
            raise PromptGuardError(
                f"Failed to load Prompt Guard model: {exc}"
            ) from exc

    async def classify(self, text: str) -> bool:
        """Classify a text chunk as benign or malicious.

        Returns True if the text is classified as "malicious" with score
        at or above the configured threshold.

        Prompt Guard 2 is a BINARY classifier with labels:
        - "benign": normal/safe text
        - "malicious": prompt injection or jailbreak attempt

        The input text MUST be pre-chunked to fit within 512 tokens.

        Raises PromptGuardError on model failure or timeout.
        Never logs the classified content.
        """
        if not text or not text.strip():
            return False

        if self._pipeline is None:
            self._load_model()

        try:
            import asyncio

            # Run synchronous model inference in a thread pool to avoid
            # blocking the event loop.
            loop = asyncio.get_running_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(None, self._pipeline, text),
                timeout=self._timeout,
            )

            if not result:
                raise PromptGuardError("Prompt Guard returned empty result")

            classification = result[0]
            label: str = classification.get("label", "benign").lower()
            score: float = classification.get("score", 0.0)

            # Prompt Guard 2 binary: "malicious" means injection or jailbreak
            is_malicious = label == "malicious" and score >= self._threshold

            if is_malicious:
                logger.debug(
                    "prompt-guard-client: classified as malicious (score above threshold)",
                )
            else:
                logger.debug("prompt-guard-client: classified as benign")

            return is_malicious

        except asyncio.TimeoutError as exc:
            raise PromptGuardError(
                f"Prompt Guard inference timed out after {self._timeout}s"
            ) from exc
        except PromptGuardError:
            raise
        except Exception as exc:
            raise PromptGuardError(
                f"Prompt Guard inference error: {type(exc).__name__}"
            ) from exc
