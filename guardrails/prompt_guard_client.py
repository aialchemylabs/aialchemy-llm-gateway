"""Fail-closed, time-bounded Prompt Guard 2 binary classifier client."""

from __future__ import annotations

import asyncio
import logging
import math
import threading
from typing import Any, Optional

from guardrails.config import (
    PROMPT_GUARD_DEVICE,
    PROMPT_GUARD_INIT_TIMEOUT_SECS,
    PROMPT_GUARD_MALICIOUS_THRESHOLD,
    PROMPT_GUARD_MODEL_ID,
    PROMPT_GUARD_MODEL_REVISION,
    PROMPT_GUARD_TIMEOUT_SECS,
)

logger = logging.getLogger(__name__)

_PINNED_ID_TO_LABEL = {0: "label_0", 1: "label_1"}
_LABEL_TO_SEMANTIC = {
    "label_0": "benign",
    "label_1": "malicious",
    "benign": "benign",
    "malicious": "malicious",
}


class PromptGuardError(Exception):
    """Raised for model load, inference, timeout, or output-contract failures."""


def _build_pipeline() -> Any:
    """Construct the classifier from the immutable model revision."""

    from transformers import pipeline as hf_pipeline
    from guardrails.prompt_guard import get_tokenizer

    return hf_pipeline(
        "text-classification",
        model=PROMPT_GUARD_MODEL_ID,
        revision=PROMPT_GUARD_MODEL_REVISION,
        tokenizer=get_tokenizer(),
        device=PROMPT_GUARD_DEVICE,
        truncation=False,
    )


class PromptGuardClient:
    """In-process Prompt Guard 2 classifier pinned to immutable artifacts."""

    MODEL_ID = PROMPT_GUARD_MODEL_ID
    MODEL_REVISION = PROMPT_GUARD_MODEL_REVISION

    def __init__(self) -> None:
        self._pipeline: Any = None
        self._threshold = PROMPT_GUARD_MALICIOUS_THRESHOLD
        self._timeout = PROMPT_GUARD_TIMEOUT_SECS
        self._init_timeout = PROMPT_GUARD_INIT_TIMEOUT_SECS

        self._load_lock = threading.Lock()
        self._load_started = False
        self._load_done = threading.Event()
        self._load_result: Any = None
        self._load_error: Optional[BaseException] = None
        self._inference_lock = threading.Lock()
        self._terminal_error: Optional[str] = None

    def _raise_if_terminal(self) -> None:
        with self._load_lock:
            reason = self._terminal_error
        if reason is not None:
            raise PromptGuardError(reason)

    def _mark_terminal(self, reason: str) -> None:
        with self._load_lock:
            if self._terminal_error is None:
                self._terminal_error = reason

    @staticmethod
    def _model_commit(classifier: Any) -> Optional[str]:
        model = getattr(classifier, "model", None)
        config = getattr(model, "config", None)
        commit = getattr(config, "_commit_hash", None)
        return commit if isinstance(commit, str) else None

    @staticmethod
    def _model_label_mapping(classifier: Any) -> dict[int, str]:
        model = getattr(classifier, "model", None)
        config = getattr(model, "config", None)
        id_to_label = getattr(config, "id2label", None)
        if not isinstance(id_to_label, dict):
            return {}

        normalized: dict[int, str] = {}
        for raw_id, raw_label in id_to_label.items():
            if not isinstance(raw_label, str):
                return {}
            try:
                label_id = int(raw_id)
            except (TypeError, ValueError):
                return {}
            if label_id in normalized:
                return {}
            normalized[label_id] = raw_label.strip().lower()
        return normalized

    def _verify_loaded_pipeline(self, classifier: Any) -> None:
        if self._model_commit(classifier) != self.MODEL_REVISION:
            raise PromptGuardError(
                "Prompt Guard model revision could not be verified"
            )
        if self._model_label_mapping(classifier) != _PINNED_ID_TO_LABEL:
            raise PromptGuardError(
                "Prompt Guard model exposes an unexpected label contract"
            )

    def _load_pipeline_in_background(self) -> None:
        try:
            classifier = _build_pipeline()
            self._verify_loaded_pipeline(classifier)
            with self._load_lock:
                self._load_result = classifier
        except BaseException as exc:
            with self._load_lock:
                self._load_error = exc
        finally:
            self._load_done.set()

    def _start_model_load(self) -> None:
        with self._load_lock:
            if self._load_started:
                return
            self._load_started = True
            threading.Thread(
                target=self._load_pipeline_in_background,
                name="prompt-guard-model-loader",
                daemon=True,
            ).start()

    def _load_model(self) -> None:
        """Load and verify the model without exceeding the configured wait."""

        self._raise_if_terminal()
        if self._pipeline is not None:
            return
        self._start_model_load()
        if not self._load_done.wait(self._init_timeout):
            self._mark_terminal("Prompt Guard model loading timed out")
            raise PromptGuardError(
                f"Prompt Guard model loading timed out after {self._init_timeout}s"
            )

        with self._load_lock:
            classifier = self._load_result
            error = self._load_error
        if classifier is None:
            raise PromptGuardError(
                "Prompt Guard model failed to load or verify"
            ) from error
        self._pipeline = classifier
        logger.info(
            "prompt-guard-client: pinned model loaded at revision %s",
            self.MODEL_REVISION,
        )

    @classmethod
    def ensure_ready(cls) -> "PromptGuardClient":
        """Eager startup/readiness helper for deployment configuration."""

        client = cls()
        client._load_model()
        return client

    def _validate_result(self, result: Any) -> tuple[str, float]:
        if not result:
            raise PromptGuardError("Prompt Guard returned an empty result")

        classification = result[0] if isinstance(result, list) else result
        if not isinstance(classification, dict):
            raise PromptGuardError("Prompt Guard returned an unexpected result type")

        raw_label = classification.get("label")
        if not isinstance(raw_label, str):
            raise PromptGuardError("Prompt Guard result has no valid label")
        label = _LABEL_TO_SEMANTIC.get(raw_label.strip().lower())
        if label is None:
            raise PromptGuardError(
                f"Prompt Guard returned unexpected label '{raw_label}'"
            )

        raw_score = classification.get("score")
        if raw_score is None:
            raise PromptGuardError("Prompt Guard result is missing a score")
        try:
            score = float(raw_score)
        except (TypeError, ValueError) as exc:
            raise PromptGuardError("Prompt Guard score is not numeric") from exc
        if not math.isfinite(score) or score < 0.0 or score > 1.0:
            raise PromptGuardError("Prompt Guard score is outside [0.0, 1.0]")

        return label, score

    def _infer_with_timeout(self, text: str) -> Any:
        """Run one inference within the bound without accumulating hung workers."""

        self._raise_if_terminal()
        if not self._inference_lock.acquire(timeout=self._timeout):
            reason = "Prompt Guard is unavailable after a stalled inference"
            self._mark_terminal(reason)
            raise PromptGuardError(reason)

        done = threading.Event()
        result: list[Any] = []
        error: list[BaseException] = []

        def run_inference() -> None:
            try:
                result.append(self._pipeline(text))
            except BaseException as exc:
                error.append(exc)
            finally:
                done.set()

        try:
            self._raise_if_terminal()
            threading.Thread(
                target=run_inference,
                name="prompt-guard-inference",
                daemon=True,
            ).start()
            if not done.wait(self._timeout):
                reason = "Prompt Guard is unavailable after an inference timeout"
                self._mark_terminal(reason)
                raise PromptGuardError(
                    f"Prompt Guard inference timed out after {self._timeout}s"
                )
            if error:
                raise PromptGuardError(
                    "Prompt Guard inference failed"
                ) from error[0]
            if not result:
                raise PromptGuardError("Prompt Guard inference returned no result")
            return result[0]
        finally:
            self._inference_lock.release()

    async def classify(self, text: str) -> bool:
        """Return True only for threshold-reaching malicious classification."""

        if not text or not text.strip():
            raise PromptGuardError("Prompt Guard received empty input")
        self._raise_if_terminal()

        loop = asyncio.get_running_loop()
        if self._pipeline is None:
            try:
                await loop.run_in_executor(None, self._load_model)
            except PromptGuardError:
                raise
            except Exception as exc:
                raise PromptGuardError("Prompt Guard model initialization failed") from exc

        try:
            result = await loop.run_in_executor(None, self._infer_with_timeout, text)
        except PromptGuardError:
            raise
        except Exception as exc:
            raise PromptGuardError("Prompt Guard inference failed") from exc

        label, score = self._validate_result(result)
        return label == "malicious" and score >= self._threshold
