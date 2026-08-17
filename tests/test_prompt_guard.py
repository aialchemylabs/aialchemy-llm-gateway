"""Tests for the Prompt Guard 2 client's fail-closed validation.

Prompt Guard 2 is a BINARY classifier: labels are exactly "benign" or
"malicious", scores are finite floats in [0, 1]. Anything else is a signal that
the model or its wiring is not what we think it is, and must block rather than
default to safe.

These tests never load the real model — the pipeline is replaced with a stub, so
they run without torch weights or HuggingFace credentials.
"""
import asyncio
import math
import unittest

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


class TestChunking(unittest.TestCase):
    """Chunking is token-based against the model's 512-token window.

    Skipped when transformers/torch are absent (host runs); executed in the
    image build where the tokenizer is available.
    """

    def setUp(self):
        try:
            import guardrails.prompt_guard as pg
        except ImportError:
            self.skipTest("transformers/torch not available on host")
        self.pg = pg

    def test_short_text_is_single_chunk(self):
        self.assertEqual(self.pg.chunk_text("short text"), ["short text"])

    def test_overlap_must_be_smaller_than_chunk_size(self):
        with self.assertRaises(ValueError):
            self.pg.chunk_text("some text", chunk_size=64, overlap=64)

    def test_chunk_limit_fails_closed(self):
        with self.assertRaises(self.pg.ChunkLimitExceeded):
            self.pg.chunk_text(
                "word " * 5000, chunk_size=16, overlap=4, max_chunks=3
            )


if __name__ == "__main__":
    unittest.main()
