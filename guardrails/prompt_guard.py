"""Pinned Prompt Guard 2 tokenizer and lossless overlapping token chunking."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional

from huggingface_hub import snapshot_download
from transformers import AutoTokenizer

from guardrails.config import (
    MAX_CHUNK_COUNT,
    PROMPT_GUARD_CHUNK_OVERLAP,
    PROMPT_GUARD_CHUNK_SIZE,
    PROMPT_GUARD_INIT_TIMEOUT_SECS,
    PROMPT_GUARD_MODEL_ID,
    PROMPT_GUARD_MODEL_REVISION,
)


class PromptGuardError(Exception):
    """Base exception for Prompt Guard preparation failures."""


class ChunkLimitExceeded(PromptGuardError):
    """Raised instead of truncating a result that needs too many chunks."""


class PromptGuardInitializationError(PromptGuardError):
    """Raised when the pinned tokenizer cannot be loaded and verified in time."""


_tokenizer: Optional[Any] = None
_tokenizer_error: Optional[BaseException] = None
_tokenizer_load_started = False
_tokenizer_load_done = threading.Event()
_tokenizer_lock = threading.Lock()


def _load_tokenizer_in_background() -> None:
    global _tokenizer, _tokenizer_error
    try:
        snapshot_path = snapshot_download(
            repo_id=PROMPT_GUARD_MODEL_ID,
            revision=PROMPT_GUARD_MODEL_REVISION,
            allow_patterns=(
                "config.json",
                "special_tokens_map.json",
                "tokenizer.json",
                "tokenizer_config.json",
            ),
        )
        if Path(snapshot_path).name != PROMPT_GUARD_MODEL_REVISION:
            raise PromptGuardInitializationError(
                "Prompt Guard tokenizer snapshot revision could not be verified"
            )
        tokenizer = AutoTokenizer.from_pretrained(
            snapshot_path,
            local_files_only=True,
            use_fast=True,
        )
        model_max_length = getattr(tokenizer, "model_max_length", None)
        if not isinstance(model_max_length, int) or model_max_length < 512:
            raise PromptGuardInitializationError(
                "Prompt Guard tokenizer reports an invalid context length"
            )
        with _tokenizer_lock:
            _tokenizer = tokenizer
    except BaseException as exc:  # stored and surfaced as a content-free error
        with _tokenizer_lock:
            _tokenizer_error = exc
    finally:
        _tokenizer_load_done.set()


def get_tokenizer() -> Any:
    """Return the immutable-revision tokenizer, bounded by the init timeout.

    The loader thread is daemonized so timing out does not immediately wait for
    a stalled network/download call during executor cleanup. Later callers
    share the same in-flight load rather than spawning duplicate downloads.
    """

    global _tokenizer_load_started
    if _tokenizer is not None:
        return _tokenizer

    with _tokenizer_lock:
        if not _tokenizer_load_started:
            _tokenizer_load_started = True
            threading.Thread(
                target=_load_tokenizer_in_background,
                name="prompt-guard-tokenizer-loader",
                daemon=True,
            ).start()

    if not _tokenizer_load_done.wait(PROMPT_GUARD_INIT_TIMEOUT_SECS):
        raise PromptGuardInitializationError(
            "Prompt Guard tokenizer loading timed out"
        )

    if _tokenizer is not None:
        return _tokenizer
    raise PromptGuardInitializationError(
        "Prompt Guard tokenizer failed to load or verify"
    ) from _tokenizer_error


def chunk_text(
    text: str,
    chunk_size: int = PROMPT_GUARD_CHUNK_SIZE,
    overlap: int = PROMPT_GUARD_CHUNK_OVERLAP,
    max_chunks: int = MAX_CHUNK_COUNT,
    tokenizer: Any | None = None,
) -> list[str]:
    """Divide text into bounded overlapping chunks without truncation."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError(
            f"overlap ({overlap}) must be non-negative and less than "
            f"chunk_size ({chunk_size})"
        )
    if max_chunks <= 0:
        raise ValueError("max_chunks must be positive")
    if not text or not text.strip():
        return [text] if text else []

    active_tokenizer = tokenizer if tokenizer is not None else get_tokenizer()
    token_ids: list[int] = active_tokenizer.encode(text, add_special_tokens=False)
    if len(token_ids) <= chunk_size:
        return [text]

    stride = chunk_size - overlap
    chunks: list[str] = []
    start = 0
    while start < len(token_ids):
        end = min(start + chunk_size, len(token_ids))
        chunks.append(
            active_tokenizer.decode(
                token_ids[start:end],
                skip_special_tokens=True,
                clean_up_tokenization_spaces=False,
            )
        )
        if len(chunks) > max_chunks:
            raise ChunkLimitExceeded(
                f"Text exceeds the configured maximum of {max_chunks} chunks"
            )
        if end >= len(token_ids):
            break
        start += stride

    return chunks
