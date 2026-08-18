"""Tests for AiAlchemyStreamRejectGuard.

Per spec §4.4 the protected route must release no uninspected final text.
Until buffered re-emission is proven, client streaming is rejected outright —
never silently coerced to non-streaming and never fake-streamed.
"""
import asyncio
import unittest

# Mock litellm before importing the guard.
import tests.conftest_guardrails  # noqa: F401

from litellm.exceptions import GuardrailRaisedException
from guardrails.stream_reject_guard import AiAlchemyStreamRejectGuard


def run(coro):
    return asyncio.run(coro)


class TestStreamRejectGuard(unittest.TestCase):
    def setUp(self):
        self.guard = AiAlchemyStreamRejectGuard()

    def test_stream_true_is_rejected(self):
        """stream=True must raise so LiteLLM returns a stable 4xx."""
        with self.assertRaises(GuardrailRaisedException) as ctx:
            run(
                self.guard.apply_guardrail(
                    inputs={},
                    request_data={"stream": True},
                    input_type="request",
                )
            )

        self.assertIn("Streaming is not supported", str(ctx.exception))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_stream_false_passes(self):
        inputs = {"texts": []}
        result = run(
            self.guard.apply_guardrail(
                inputs=inputs,
                request_data={"stream": False},
                input_type="request",
            )
        )
        self.assertEqual(result, inputs)

    def test_stream_absent_passes(self):
        inputs = {"texts": []}
        result = run(
            self.guard.apply_guardrail(
                inputs=inputs, request_data={}, input_type="request"
            )
        )
        self.assertEqual(result, inputs)

    def test_request_is_not_silently_coerced(self):
        """The guard must NOT rewrite stream to False — it must reject."""
        request_data = {"stream": True}

        with self.assertRaises(GuardrailRaisedException):
            run(
                self.guard.apply_guardrail(
                    inputs={}, request_data=request_data, input_type="request"
                )
            )

        # stream is left exactly as the client sent it; no coercion.
        self.assertIs(request_data["stream"], True)

    def test_truthy_non_true_value_is_not_rejected(self):
        """Only an explicit True is a streaming request.

        Documents the current contract: LiteLLM normalises `stream` to a bool
        before guardrails run, so an identity check is intentional here.
        """
        inputs = {"texts": []}
        result = run(
            self.guard.apply_guardrail(
                inputs=inputs, request_data={"stream": "yes"}, input_type="request"
            )
        )
        self.assertEqual(result, inputs)
if __name__ == "__main__":
    unittest.main()
