"""Tests for AiAlchemyPiiInputGuard.

Proves the guard works through LiteLLM's inputs['texts'] return path — the
mechanism that actually survives LiteLLM's write-back — plus raw mutation of
the fields LiteLLM never extracts (instructions, function_call_output.output).
"""
import asyncio
import unittest
from unittest.mock import AsyncMock

# Mock litellm before importing the guard.
import tests.conftest_guardrails  # noqa: F401

from guardrails.pii_input_guard import AiAlchemyPiiInputGuard
from guardrails.presidio_client import PresidioError


def run(coro):
    return asyncio.run(coro)


class TestPiiInputGuardTextsPath(unittest.TestCase):
    """inputs['texts'] is the authoritative channel LiteLLM writes back."""

    def setUp(self):
        self.guard = AiAlchemyPiiInputGuard()
        self.guard._presidio = AsyncMock()

    def test_returns_transformed_texts(self):
        """Masked text is returned in inputs['texts'], not just mutated in request_data."""
        self.guard._presidio.analyze_and_anonymize = AsyncMock(
            return_value="Hello <PERSON>"
        )
        inputs = {"texts": ["Hello John Smith"]}

        result = run(
            self.guard.apply_guardrail(
                inputs=inputs,
                request_data={"input": [{"role": "user", "content": "Hello John Smith"}]},
                input_type="request",
            )
        )

        self.assertEqual(result["texts"], ["Hello <PERSON>"])

    def test_preserves_order_and_cardinality(self):
        """Returned texts keep exact input order and length."""
        async def fake(text):
            return f"masked:{text}"

        self.guard._presidio.analyze_and_anonymize = fake
        inputs = {"texts": ["first", "second", "third"]}

        result = run(
            self.guard.apply_guardrail(
                inputs=inputs, request_data={}, input_type="request"
            )
        )

        self.assertEqual(
            result["texts"], ["masked:first", "masked:second", "masked:third"]
        )
        self.assertEqual(len(result["texts"]), 3)

    def test_empty_texts_is_not_an_error(self):
        """A tool-only continuation has no extracted texts — that must not raise."""
        self.guard._presidio.analyze_and_anonymize = AsyncMock(return_value="x")
        inputs = {"texts": []}

        result = run(
            self.guard.apply_guardrail(
                inputs=inputs, request_data={}, input_type="request"
            )
        )

        self.assertEqual(result["texts"], [])


class TestPiiInputGuardRawFields(unittest.TestCase):
    """Fields LiteLLM does not extract are mutated on request_data directly."""

    def setUp(self):
        self.guard = AiAlchemyPiiInputGuard()
        self.guard._presidio = AsyncMock()

    def test_masks_instructions(self):
        """The Responses `instructions` field is masked."""
        self.guard._presidio.analyze_and_anonymize = AsyncMock(
            return_value="Contact <EMAIL_ADDRESS>"
        )
        request_data = {"instructions": "Contact john@example.com"}

        run(
            self.guard.apply_guardrail(
                inputs={"texts": []}, request_data=request_data, input_type="request"
            )
        )

        self.assertEqual(request_data["instructions"], "Contact <EMAIL_ADDRESS>")

    def test_masks_function_call_output(self):
        """function_call_output.output is masked via raw mutation.

        LiteLLM never extracts this field into texts, so the guard must reach it
        through request_data or the PII would reach the provider unmasked.
        """
        self.guard._presidio.analyze_and_anonymize = AsyncMock(
            return_value="mail <EMAIL_ADDRESS>"
        )
        request_data = {
            "input": [
                {
                    "type": "function_call_output",
                    "call_id": "c1",
                    "output": "mail secret@example.com",
                }
            ]
        }

        run(
            self.guard.apply_guardrail(
                inputs={"texts": []}, request_data=request_data, input_type="request"
            )
        )

        self.assertEqual(
            request_data["input"][0]["output"], "mail <EMAIL_ADDRESS>"
        )

    def test_non_string_function_call_output_untouched(self):
        """Structured output is left for the web-tool guard to adjudicate."""
        self.guard._presidio.analyze_and_anonymize = AsyncMock(return_value="nope")
        structured = [{"type": "image", "data": "..."}]
        request_data = {
            "input": [
                {"type": "function_call_output", "call_id": "c1", "output": structured}
            ]
        }

        run(
            self.guard.apply_guardrail(
                inputs={"texts": []}, request_data=request_data, input_type="request"
            )
        )

        self.assertEqual(request_data["input"][0]["output"], structured)


class TestPiiInputGuardFailClosed(unittest.TestCase):
    def setUp(self):
        self.guard = AiAlchemyPiiInputGuard()
        self.guard._presidio = AsyncMock()

    def test_presidio_error_blocks_request(self):
        """Any Presidio failure blocks — never forwards unmasked content."""
        self.guard._presidio.analyze_and_anonymize = AsyncMock(
            side_effect=PresidioError("connection refused")
        )

        with self.assertRaises(RuntimeError) as ctx:
            run(
                self.guard.apply_guardrail(
                    inputs={"texts": ["Hello John Smith"]},
                    request_data={},
                    input_type="request",
                )
            )

        self.assertIn("blocked", str(ctx.exception))

    def test_unexpected_error_blocks_request(self):
        """A non-Presidio exception still fails closed."""
        self.guard._presidio.analyze_and_anonymize = AsyncMock(
            side_effect=ValueError("boom")
        )

        with self.assertRaises(RuntimeError):
            run(
                self.guard.apply_guardrail(
                    inputs={"texts": ["text"]},
                    request_data={},
                    input_type="request",
                )
            )

    def test_response_type_is_passthrough(self):
        """The input guard ignores responses — the output guard owns those."""
        self.guard._presidio.analyze_and_anonymize = AsyncMock(return_value="nope")
        inputs = {"texts": ["some response text"]}

        result = run(
            self.guard.apply_guardrail(
                inputs=inputs, request_data={}, input_type="response"
            )
        )

        self.assertEqual(result, inputs)
        self.guard._presidio.analyze_and_anonymize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
