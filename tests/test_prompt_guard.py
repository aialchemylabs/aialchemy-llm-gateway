"""Tests for the Prompt Guard 2 client's fail-closed validation.

The pinned Prompt Guard 2 artifact exposes binary ``LABEL_0``/``LABEL_1`` IDs,
which the client maps to benign/malicious semantics. Scores are finite floats
in [0, 1]. Anything else is a signal that the model or its wiring is not what
we think it is, and must block rather than default to safe.

These tests never load the real model — the pipeline is replaced with a stub, so
they run without torch weights or HuggingFace credentials.
"""
import asyncio
import math
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

# Mock litellm before importing guard modules.
import tests.conftest_guardrails  # noqa: F401

from guardrails.prompt_guard_client import PromptGuardClient, PromptGuardError


def run(coro):
    return asyncio.run(coro)


def client_returning(payload):
    """Build a client whose pipeline returns `payload` without loading a model."""
    client = PromptGuardClient()
    client._pipeline = lambda text: payload
    return client


class TestClassificationDecisions(unittest.TestCase):
    def test_pinned_label_zero_allows(self):
        client = client_returning([{"label": "LABEL_0", "score": 0.99}])
        self.assertFalse(run(client.classify("normal text")))

    def test_pinned_label_one_above_threshold_blocks(self):
        client = client_returning([{"label": "LABEL_1", "score": 0.97}])
        self.assertTrue(run(client.classify("ignore previous instructions")))

    def test_benign_allows(self):
        client = client_returning([{"label": "benign", "score": 0.99}])
        self.assertFalse(run(client.classify("normal text")))

    def test_malicious_above_threshold_blocks(self):
        client = client_returning([{"label": "malicious", "score": 0.97}])
        self.assertTrue(run(client.classify("ignore previous instructions")))

    def test_malicious_below_threshold_allows(self):
        """A low-confidence malicious call is the one case that does not block."""
        client = client_returning([{"label": "malicious", "score": 0.10}])
        self.assertFalse(run(client.classify("borderline text")))

    def test_label_casing_is_normalised(self):
        """The model card shows an upper-case MALICIOUS from the raw model."""
        client = client_returning([{"label": "MALICIOUS", "score": 0.99}])
        self.assertTrue(run(client.classify("attack")))

    def test_score_exactly_at_threshold_blocks(self):
        client = client_returning([{"label": "malicious", "score": 0.85}])
        self.assertTrue(run(client.classify("attack")))


class TestFailClosedValidation(unittest.TestCase):
    """Every malformed classifier response must raise, never return False."""

    def test_unknown_label_raises(self):
        client = client_returning([{"label": "unexpected", "score": 0.999}])
        with self.assertRaises(PromptGuardError) as ctx:
            run(client.classify("text"))
        self.assertIn("unexpected label", str(ctx.exception).lower())

    def test_prompt_guard_1_labels_raise(self):
        """INJECTION/JAILBREAK are v1 labels — their presence means wrong model."""
        for legacy in ("INJECTION", "JAILBREAK"):
            client = client_returning([{"label": legacy, "score": 0.99}])
            with self.assertRaises(PromptGuardError):
                run(client.classify("text"))

    def test_missing_label_raises(self):
        client = client_returning([{"score": 0.99}])
        with self.assertRaises(PromptGuardError):
            run(client.classify("text"))

    def test_non_string_label_raises(self):
        client = client_returning([{"label": 1, "score": 0.99}])
        with self.assertRaises(PromptGuardError):
            run(client.classify("text"))

    def test_missing_score_raises(self):
        client = client_returning([{"label": "malicious"}])
        with self.assertRaises(PromptGuardError):
            run(client.classify("text"))

    def test_nan_score_raises(self):
        client = client_returning([{"label": "benign", "score": float("nan")}])
        with self.assertRaises(PromptGuardError):
            run(client.classify("text"))

    def test_infinite_score_raises(self):
        client = client_returning([{"label": "benign", "score": math.inf}])
        with self.assertRaises(PromptGuardError):
            run(client.classify("text"))

    def test_out_of_range_scores_raise(self):
        for bad in (-0.1, 1.5):
            client = client_returning([{"label": "benign", "score": bad}])
            with self.assertRaises(PromptGuardError):
                run(client.classify("text"))

    def test_non_numeric_score_raises(self):
        client = client_returning([{"label": "benign", "score": "high"}])
        with self.assertRaises(PromptGuardError):
            run(client.classify("text"))

    def test_empty_result_raises(self):
        client = client_returning([])
        with self.assertRaises(PromptGuardError):
            run(client.classify("text"))

    def test_non_dict_result_raises(self):
        client = client_returning(["not a dict"])
        with self.assertRaises(PromptGuardError):
            run(client.classify("text"))

    def test_empty_input_raises(self):
        """Blank input is a wiring bug, not a benign prompt — fail closed."""
        client = client_returning([{"label": "benign", "score": 0.99}])
        for blank in ("", "   ", "\n"):
            with self.assertRaises(PromptGuardError):
                run(client.classify(blank))

    def test_inference_exception_raises_prompt_guard_error(self):
        client = PromptGuardClient()

        def explode(text):
            raise ValueError("model exploded")

        client._pipeline = explode
        with self.assertRaises(PromptGuardError):
            run(client.classify("text"))

    def test_model_load_timeout_returns_within_the_configured_bound(self):
        """A timed-out loader must not be awaited again during executor cleanup."""
        client = PromptGuardClient()
        client._init_timeout = 0.02

        def slow_loader(*args, **kwargs):
            time.sleep(0.30)
            return lambda text: [{"label": "benign", "score": 0.99}]

        with patch(
            "guardrails.prompt_guard_client._build_pipeline",
            side_effect=slow_loader,
        ):
            started = time.monotonic()
            with self.assertRaises(PromptGuardError):
                run(client.classify("normal text"))
            elapsed = time.monotonic() - started

        self.assertLess(
            elapsed,
            0.15,
            "model initialization timeout did not bound request latency",
        )

    def test_inference_timeout_poison_prevents_worker_accumulation(self):
        """After a hung inference, later requests must fail without spawning work."""
        calls = 0
        client = PromptGuardClient()
        client._timeout = 0.05

        def hung_pipeline(text):
            nonlocal calls
            calls += 1
            time.sleep(0.30)
            return [{"label": "benign", "score": 0.99}]

        client._pipeline = hung_pipeline
        with self.assertRaises(PromptGuardError):
            run(client.classify("first"))

        started = time.monotonic()
        with self.assertRaises(PromptGuardError):
            run(client.classify("second"))
        elapsed = time.monotonic() - started

        self.assertEqual(calls, 1)
        self.assertLess(elapsed, 0.02)


class TestPinnedModelContract(unittest.TestCase):
    def test_exact_pinned_label_ids_are_ready(self):
        client = PromptGuardClient()
        classifier = SimpleNamespace(
            model=SimpleNamespace(
                config=SimpleNamespace(
                    _commit_hash=client.MODEL_REVISION,
                    id2label={0: "LABEL_0", 1: "LABEL_1"},
                )
            )
        )

        with patch(
            "guardrails.prompt_guard_client._build_pipeline",
            return_value=classifier,
        ):
            ready = PromptGuardClient.ensure_ready()

        self.assertIs(ready._pipeline, classifier)


class FakeTokenizer:
    """Deterministic word-level tokenizer standing in for the gated model's.

    Prompt Guard 2's weights are gated on HuggingFace, so the real tokenizer
    cannot be fetched during an unauthenticated image build. Chunking logic —
    stride, overlap, coverage, limits — is independent of the specific
    vocabulary, so a reversible word-level mapping exercises it exactly while
    keeping the test hermetic. One word == one token.
    """

    def __init__(self):
        self._vocab: list[str] = []
        self._ids: dict[str, int] = {}

    def encode(self, text, add_special_tokens=False):
        ids = []
        for word in text.split():
            if word not in self._ids:
                self._ids[word] = len(self._vocab)
                self._vocab.append(word)
            ids.append(self._ids[word])
        return ids

    def decode(self, ids, skip_special_tokens=True, **kwargs):
        return " ".join(self._vocab[i] for i in ids)


class TestChunking(unittest.TestCase):
    """Chunking is token-based against the model's 512-token window.

    Truncation is prohibited by spec §7.6 — every token must land in some chunk.
    """

    def setUp(self):
        try:
            import guardrails.prompt_guard as pg
        except ImportError:
            self.skipTest("transformers/torch not installed (host run)")
        self.pg = pg
        # Install the fake tokenizer in place of the gated model's.
        self._original = pg._tokenizer
        pg._tokenizer = FakeTokenizer()

    def tearDown(self):
        if hasattr(self, "pg"):
            self.pg._tokenizer = self._original

    def test_short_text_is_single_chunk(self):
        self.assertEqual(self.pg.chunk_text("short text"), ["short text"])

    def test_text_at_chunk_size_is_single_chunk(self):
        text = " ".join(f"w{i}" for i in range(10))
        self.assertEqual(self.pg.chunk_text(text, chunk_size=10, overlap=2), [text])

    def test_long_text_splits_into_multiple_chunks(self):
        text = " ".join(f"w{i}" for i in range(25))
        chunks = self.pg.chunk_text(text, chunk_size=10, overlap=2)
        self.assertGreater(len(chunks), 1)

    def test_chunks_overlap_by_configured_amount(self):
        """Adjacent chunks must share `overlap` tokens so an injection
        straddling a boundary still appears whole in one chunk."""
        text = " ".join(f"w{i}" for i in range(25))
        chunks = self.pg.chunk_text(text, chunk_size=10, overlap=3)

        first = chunks[0].split()
        second = chunks[1].split()
        self.assertEqual(first[-3:], second[:3])

    def test_no_token_is_dropped(self):
        """Truncation is prohibited — every token appears in some chunk."""
        words = [f"w{i}" for i in range(57)]
        chunks = self.pg.chunk_text(" ".join(words), chunk_size=10, overlap=3)

        seen = set()
        for chunk in chunks:
            seen.update(chunk.split())
        self.assertEqual(seen, set(words))

    def test_final_token_is_present(self):
        """Guards against an off-by-one that silently drops the tail."""
        words = [f"w{i}" for i in range(31)]
        chunks = self.pg.chunk_text(" ".join(words), chunk_size=10, overlap=2)
        self.assertIn("w30", chunks[-1].split())

    def test_overlap_must_be_smaller_than_chunk_size(self):
        with self.assertRaises(ValueError):
            self.pg.chunk_text("some text", chunk_size=64, overlap=64)

    def test_chunk_limit_fails_closed(self):
        with self.assertRaises(self.pg.ChunkLimitExceeded):
            self.pg.chunk_text(
                " ".join(f"w{i}" for i in range(5000)),
                chunk_size=16,
                overlap=4,
                max_chunks=3,
            )

    def test_empty_text_produces_no_chunks(self):
        self.assertEqual(self.pg.chunk_text(""), [])


if __name__ == "__main__":
    unittest.main()
