"""Tests for AiAlchemyPiiInputGuard.

Verifies PII is masked in user messages and Responses API input before
the provider call, and that errors fail closed (block the request).
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


GUARD_MODULE = "guardrails.pii_input_guard"


def run_async(coro):
    """Helper to run async test methods."""
    return asyncio.get_event_loop().run_until_complete(coro)


class PiiInputGuardTests(unittest.TestCase):
    """Unit tests for AiAlchemyPiiInputGuard (aialchemy-pii-input-v1)."""

    def _get_module(self):
        import importlib

        return importlib.import_module(GUARD_MODULE)

    def _make_guard(self):
        """Create a PiiInputGuard instance."""
        mod = self._get_module()
        return mod.AiAlchemyPiiInputGuard()

    def _make_chat_request(self, user_message: str) -> dict:
        """Build a minimal chat/Responses request with a user message."""
        return {
            "input": [
                {"role": "user", "content": user_message},
            ]
        }

    def _make_responses_request(self, items: list) -> dict:
        """Build a Responses API request with arbitrary input items."""
        return {"input": items}

    @patch(f"{GUARD_MODULE}.presidio_anonymize")
    def test_masks_pii_in_user_message(self, mock_anonymize) -> None:
        """PII in a user message is masked before forwarding to the provider."""
        masked_text = "My name is <PERSON> and my email is <EMAIL_ADDRESS>."

        async def mock_anon(text):
            return {"text": masked_text}

        mock_anonymize.side_effect = lambda text: mock_anon(text)

        guard = self._make_guard()
        request_data = self._make_chat_request(
            "My name is John Doe and my email is john@example.com."
        )

        async def _test():
            result = await guard.inspect(request_data)
            self.assertEqual(result.action, "ALLOW")
            # The user message should be rewritten with masked PII
            user_items = [
                item for item in request_data["input"]
                if item.get("role") == "user"
            ]
            self.assertEqual(user_items[0]["content"], masked_text)

        run_async(_test())

    @patch(f"{GUARD_MODULE}.presidio_anonymize")
    def test_masks_pii_in_responses_api_input(self, mock_anonymize) -> None:
        """PII in Responses API structured input items is masked."""
        masked_text = "Contact <PERSON> at <PHONE_NUMBER>"

        async def mock_anon(text):
            return {"text": masked_text}

        mock_anonymize.side_effect = lambda text: mock_anon(text)

        guard = self._make_guard()
        request_data = self._make_responses_request([
            {"role": "user", "content": "Contact Alice at 0412-345-678"},
        ])

        async def _test():
            result = await guard.inspect(request_data)
            self.assertEqual(result.action, "ALLOW")
            user_items = [
                item for item in request_data["input"]
                if item.get("role") == "user"
            ]
            self.assertEqual(user_items[0]["content"], masked_text)

        run_async(_test())

    @patch(f"{GUARD_MODULE}.presidio_anonymize")
    def test_presidio_error_blocks_request(self, mock_anonymize) -> None:
        """A Presidio error causes the input guard to fail closed (BLOCK)."""
        from guardrails.presidio_client import PresidioError

        mock_anonymize.side_effect = PresidioError("Service unavailable")

        guard = self._make_guard()
        request_data = self._make_chat_request("Some message with PII")

        async def _test():
            result = await guard.inspect(request_data)
            self.assertEqual(result.action, "BLOCK")

        run_async(_test())

    @patch(f"{GUARD_MODULE}.presidio_anonymize")
    def test_no_pii_passes_through_unchanged(self, mock_anonymize) -> None:
        """Text with no PII passes through without modification."""
        original_text = "What is the weather in Sydney today?"

        async def mock_anon(text):
            # No PII found -- return text unchanged
            return {"text": text}

        mock_anonymize.side_effect = lambda text: mock_anon(text)

        guard = self._make_guard()
        request_data = self._make_chat_request(original_text)

        async def _test():
            result = await guard.inspect(request_data)
            self.assertEqual(result.action, "ALLOW")
            user_items = [
                item for item in request_data["input"]
                if item.get("role") == "user"
            ]
            self.assertEqual(user_items[0]["content"], original_text)

        run_async(_test())


if __name__ == "__main__":
    unittest.main()
