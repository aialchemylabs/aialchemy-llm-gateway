"""Tests for AiAlchemyPiiOutputGuard.

The output guard is stateless per request and Presidio-only — per spec §4.4
Prompt Guard must never inspect a final provider answer. On any failure it
raises so no partial text is released.
"""
import asyncio
import unittest
from unittest.mock import AsyncMock

# Mock litellm before importing the guard.
import tests.conftest_guardrails  # noqa: F401

from guardrails.pii_output_guard import AiAlchemyPiiOutputGuard
from guardrails.presidio_client import PresidioError


def run(coro):
    return asyncio.run(coro)


class TestPiiOutputGuard(unittest.TestCase):
    def setUp(self):
        self.guard = AiAlchemyPiiOutputGuard()
        self.guard._presidio = AsyncMock()

    def test_masks_response_texts(self):
        self.guard._presidio.analyze_and_anonymize = AsyncMock(
            return_value="Reach <EMAIL_ADDRESS>"
        )
        inputs = {"texts": ["Reach john@example.com"]}

        result = run(
            self.guard.apply_guardrail(
                inputs=inputs, request_data={}, input_type="response"
            )
        )

        self.assertEqual(result["texts"], ["Reach <EMAIL_ADDRESS>"])

    def test_preserves_order_and_cardinality(self):
        async def fake(text):
            return f"masked:{text}"

        self.guard._presidio.analyze_and_anonymize = fake
        inputs = {"texts": ["one", "two"]}

        result = run(
            self.guard.apply_guardrail(
                inputs=inputs, request_data={}, input_type="response"
            )
        )

        self.assertEqual(result["texts"], ["masked:one", "masked:two"])

    def test_empty_string_preserved(self):
        self.guard._presidio.analyze_and_anonymize = AsyncMock(return_value="x")
        inputs = {"texts": ["", "real"]}

        result = run(
            self.guard.apply_guardrail(
                inputs=inputs, request_data={}, input_type="response"
            )
        )

        self.assertEqual(result["texts"][0], "")

    def test_error_blocks_and_releases_nothing(self):
        """A Presidio failure raises — no partially masked list is returned."""
        self.guard._presidio.analyze_and_anonymize = AsyncMock(
            side_effect=PresidioError("timeout")
        )
        inputs = {"texts": ["contains john@example.com"]}

        with self.assertRaises(RuntimeError) as ctx:
            run(
                self.guard.apply_guardrail(
                    inputs=inputs, request_data={}, input_type="response"
                )
            )

        message = str(ctx.exception)
        self.assertIn("blocked", message)
        # The original text was never swapped into the returned structure.
        self.assertEqual(inputs["texts"], ["contains john@example.com"])

    def test_request_type_is_passthrough(self):
        """The output guard ignores requests — the input guard owns those."""
        self.guard._presidio.analyze_and_anonymize = AsyncMock(return_value="nope")
        inputs = {"texts": ["prompt text"]}

        result = run(
            self.guard.apply_guardrail(
                inputs=inputs, request_data={}, input_type="request"
            )
        )

        self.assertEqual(result, inputs)
        self.guard._presidio.analyze_and_anonymize.assert_not_called()

    def test_no_texts_is_not_an_error(self):
        self.guard._presidio.analyze_and_anonymize = AsyncMock(return_value="x")
        inputs = {"texts": []}

        result = run(
            self.guard.apply_guardrail(
                inputs=inputs, request_data={}, input_type="response"
            )
        )

        self.assertEqual(result["texts"], [])

    def test_guard_is_stateless_across_calls(self):
        """No buffer state may leak between requests."""
        async def fake(text):
            return f"masked:{text}"

        self.guard._presidio.analyze_and_anonymize = fake

        first = run(
            self.guard.apply_guardrail(
                inputs={"texts": ["a"]}, request_data={}, input_type="response"
            )
        )
        second = run(
            self.guard.apply_guardrail(
                inputs={"texts": ["b"]}, request_data={}, input_type="response"
            )
        )

        self.assertEqual(first["texts"], ["masked:a"])
        self.assertEqual(second["texts"], ["masked:b"])


if __name__ == "__main__":
    unittest.main()
