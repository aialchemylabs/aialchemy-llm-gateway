"""Tests for AiAlchemyPiiOutputGuard.

Verifies PII is masked in final provider responses before the user receives them,
and that errors fail closed (releasing no partial text).
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


GUARD_MODULE = "guardrails.pii_output_guard"


def run_async(coro):
    """Helper to run async test methods."""
    return asyncio.get_event_loop().run_until_complete(coro)


class PiiOutputGuardTests(unittest.TestCase):
    """Unit tests for AiAlchemyPiiOutputGuard (aialchemy-pii-output-v1)."""

    def _get_module(self):
        import importlib

        return importlib.import_module(GUARD_MODULE)

    def _make_guard(self):
        """Create a PiiOutputGuard instance."""
        mod = self._get_module()
        return mod.AiAlchemyPiiOutputGuard()

    def _make_response_data(self, text: str) -> dict:
        """Build a minimal provider response structure."""
        return {
            "output": [
                {"type": "message", "content": [{"type": "text", "text": text}]}
            ]
        }

    @patch(f"{GUARD_MODULE}.presidio_anonymize")
    def test_masks_pii_in_response_text(self, mock_anonymize) -> None:
        """PII in the provider's final response text is masked before the user receives it."""
        masked_text = "The account belongs to <PERSON> at <EMAIL_ADDRESS>."

        async def mock_anon(text):
            return {"text": masked_text}

        mock_anonymize.side_effect = lambda text: mock_anon(text)

        guard = self._make_guard()
        response_data = self._make_response_data(
            "The account belongs to Jane Smith at jane.smith@corp.com."
        )

        async def _test():
            result = await guard.inspect(response_data)
            self.assertEqual(result.action, "ALLOW")
            # Verify the response text was rewritten
            output_items = response_data["output"]
            text_content = output_items[0]["content"][0]["text"]
            self.assertEqual(text_content, masked_text)
            self.assertNotIn("Jane Smith", text_content)
            self.assertNotIn("jane.smith@corp.com", text_content)

        run_async(_test())

    @patch(f"{GUARD_MODULE}.presidio_anonymize")
    def test_error_blocks_response(self, mock_anonymize) -> None:
        """A Presidio error causes the output guard to fail closed -- no partial text released."""
        from guardrails.presidio_client import PresidioError

        mock_anonymize.side_effect = PresidioError("Anonymizer crashed")

        guard = self._make_guard()
        response_data = self._make_response_data(
            "The patient record for John Smith shows TFN 123456789."
        )

        async def _test():
            result = await guard.inspect(response_data)
            self.assertEqual(result.action, "BLOCK")
            # No partial text should be released to the user -- the guard
            # must not forward the original unmasked response

        run_async(_test())

    @patch(f"{GUARD_MODULE}.presidio_anonymize")
    def test_no_pii_passes_through(self, mock_anonymize) -> None:
        """Response text with no PII passes through unchanged."""
        original_text = "The current temperature in Sydney is 24 degrees Celsius."

        async def mock_anon(text):
            return {"text": text}

        mock_anonymize.side_effect = lambda text: mock_anon(text)

        guard = self._make_guard()
        response_data = self._make_response_data(original_text)

        async def _test():
            result = await guard.inspect(response_data)
            self.assertEqual(result.action, "ALLOW")
            output_items = response_data["output"]
            text_content = output_items[0]["content"][0]["text"]
            self.assertEqual(text_content, original_text)

        run_async(_test())


if __name__ == "__main__":
    unittest.main()
