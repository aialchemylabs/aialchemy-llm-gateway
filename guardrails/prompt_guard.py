"""Prompt Guard 2 86M integration for prompt injection/jailbreak detection.

Uses Meta's Prompt Guard 2 (mDeBERTa-v3-base encoder-only classifier) to classify
text segments as benign or malicious. Text is chunked by TOKEN count with overlap
to ensure no content is missed and no injection can hide at boundaries.

Prompt Guard 2 is a BINARY classifier:
- Labels: "benign" or "malicious"
- Context window: 512 tokens
- License: Llama 4 Community License

Text longer than 512 tokens MUST be divided into overlapping chunks.
Truncation is PROHIBITED — all text must be covered.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

import torch
from transformers import AutoTokenizer, pipeline

from guardrails.config import (
    MAX_CHUNK_COUNT,
    PROMPT_GUARD_CHUNK_OVERLAP,
    PROMPT_GUARD_CHUNK_SIZE,
    PROMPT_GUARD_DEVICE,
    PROMPT_GUARD_MODEL_ID,
    PROMPT_GUARD_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PromptGuardError(Exception):
    """Base exception for Prompt Guard operations."""


class ChunkLimitExceeded(PromptGuardError):
    """Raised when input text produces more chunks than the configured maximum.

    This is a fail-closed safety measure: excessively long inputs are rejected
    rather than silently truncated.
    """


class ClassificationTimeout(PromptGuardError):
    """Raised when classification exceeds the allowed time budget."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    """Result of classifying a single text chunk.

    Attributes:
        label: "benign" or "malicious" (Prompt Guard 2 binary output).
        score: Model confidence in [0, 1].
        chunk_index: Zero-based index of the chunk within the original text.
    """

    label: str
    score: float
    chunk_index: int


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def chunk_text(
    text: str,
    chunk_size: int = PROMPT_GUARD_CHUNK_SIZE,
    overlap: int = PROMPT_GUARD_CHUNK_OVERLAP,
    max_chunks: int = MAX_CHUNK_COUNT,
) -> list[str]:
    """Divide text into bounded, overlapping chunks by token count.

    Uses the Prompt Guard model's tokenizer to split text into chunks of at most
    `chunk_size` tokens, with `overlap` tokens shared between consecutive chunks.
    The model's context window is 512 tokens — chunk_size defaults to 512.
    Truncation is PROHIBITED — all text is covered.

    Args:
        text: The input text to chunk.
        chunk_size: Maximum number of tokens per chunk (default: 512, the model limit).
        overlap: Number of overlapping tokens between consecutive chunks.
        max_chunks: Hard limit on chunk count. Exceeding raises ChunkLimitExceeded.

    Returns:
        List of text chunks covering the entire input.

    Raises:
        ChunkLimitExceeded: If the text would produce more than max_chunks chunks.
        ValueError: If overlap >= chunk_size.
    """
    if overlap >= chunk_size:
        raise ValueError(
            f"overlap ({overlap}) must be less than chunk_size ({chunk_size})"
        )

    if not text or not text.strip():
        return [text] if text else []

    tokenizer = _get_tokenizer()

    # Encode without special tokens to get raw token ids
    token_ids: list[int] = tokenizer.encode(text, add_special_tokens=False)

    if len(token_ids) <= chunk_size:
        return [text]

    stride = chunk_size - overlap
    chunks: list[str] = []
    start = 0

    while start < len(token_ids):
        end = min(start + chunk_size, len(token_ids))
        chunk_ids = token_ids[start:end]
        chunk_text_decoded = tokenizer.decode(chunk_ids, skip_special_tokens=True)
        chunks.append(chunk_text_decoded)

        if end >= len(token_ids):
            break
        start += stride

    if len(chunks) > max_chunks:
        raise ChunkLimitExceeded(
            f"Text produced {len(chunks)} chunks, exceeding max_chunks={max_chunks}. "
            f"Refusing to process — fail closed."
        )

    return chunks


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


class PromptGuardClassifier:
    """Wrapper around the Prompt Guard 2 86M text-classification pipeline.

    Provides single-text and batch classification, plus a maliciousness check
    against a configurable confidence threshold.

    Prompt Guard 2 is BINARY: labels are "benign" and "malicious".
    """

    def __init__(
        self,
        model_id: str = PROMPT_GUARD_MODEL_ID,
        device: str = PROMPT_GUARD_DEVICE,
        threshold: float = PROMPT_GUARD_THRESHOLD,
    ) -> None:
        """Initialize the classifier by loading the model pipeline.

        Args:
            model_id: HuggingFace model identifier for Prompt Guard 2 86M.
            device: Torch device string ('cpu', 'cuda', 'mps', etc.).
            threshold: Score threshold above which "malicious" results block.
        """
        self.model_id = model_id
        self.device = device
        self.threshold = threshold
        self._pipeline = pipeline(
            "text-classification",
            model=model_id,
            device=device,
            torch_dtype=torch.float32,
            truncation=True,
            max_length=512,  # Model context window
        )

    def classify(self, text: str) -> ClassificationResult:
        """Classify a single text segment.

        Args:
            text: Input text to classify (must be <= 512 tokens).

        Returns:
            ClassificationResult with the top label, its score, and chunk_index=0.
        """
        results = self._pipeline(text)
        # pipeline returns a list with one dict: {'label': str, 'score': float}
        top = results[0]
        return ClassificationResult(
            label=top["label"].lower(),
            score=top["score"],
            chunk_index=0,
        )

    def classify_chunks(self, chunks: list[str]) -> list[ClassificationResult]:
        """Classify multiple text chunks in batch.

        Args:
            chunks: List of text segments to classify (each <= 512 tokens).

        Returns:
            List of ClassificationResult, one per chunk, preserving order.
        """
        if not chunks:
            return []

        batch_results = self._pipeline(chunks)

        classification_results: list[ClassificationResult] = []
        for idx, result in enumerate(batch_results):
            # Each result is a dict: {'label': str, 'score': float}
            if isinstance(result, list):
                # Shouldn't happen with default top_k=1 but defensive
                result = result[0]
            classification_results.append(
                ClassificationResult(
                    label=result["label"].lower(),
                    score=result["score"],
                    chunk_index=idx,
                )
            )

        return classification_results

    def is_malicious(self, result: ClassificationResult) -> bool:
        """Determine if a classification result indicates a malicious prompt.

        A result is malicious if the label is "malicious" AND the confidence
        score meets or exceeds the threshold.

        Args:
            result: A ClassificationResult to evaluate.

        Returns:
            True if classified as malicious with sufficient confidence.
        """
        return result.label == "malicious" and result.score >= self.threshold


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_classifier: Optional[PromptGuardClassifier] = None
_classifier_lock = threading.Lock()
_tokenizer: Optional[AutoTokenizer] = None
_tokenizer_lock = threading.Lock()


def get_classifier() -> PromptGuardClassifier:
    """Get or create the module-level PromptGuardClassifier singleton.

    Thread-safe lazy initialization using config values from guardrails.config.

    Returns:
        The shared PromptGuardClassifier instance.
    """
    global _classifier
    if _classifier is None:
        with _classifier_lock:
            if _classifier is None:
                _classifier = PromptGuardClassifier(
                    model_id=PROMPT_GUARD_MODEL_ID,
                    device=PROMPT_GUARD_DEVICE,
                    threshold=PROMPT_GUARD_THRESHOLD,
                )
    return _classifier


def _get_tokenizer() -> AutoTokenizer:
    """Get or create the module-level tokenizer singleton for chunking.

    Uses the same model_id as the classifier to ensure token counts align
    with the model's actual vocabulary.

    Returns:
        The shared AutoTokenizer instance.
    """
    global _tokenizer
    if _tokenizer is None:
        with _tokenizer_lock:
            if _tokenizer is None:
                _tokenizer = AutoTokenizer.from_pretrained(PROMPT_GUARD_MODEL_ID)
    return _tokenizer
