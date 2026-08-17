"""Tests for PresidioClient with mocked httpx responses.

Verifies the client correctly calls Presidio analyzer/anonymizer endpoints,
handles typed replacement (numbered persons), and fails closed on errors.
"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import asyncio


# Expected module location for the Presidio client
PRESIDIO_MODULE = "guardrails.presidio_client"


def run_async(coro):
    """Helper to run async test methods."""
    return asyncio.run(coro)


class PresidioClientTests(unittest.TestCase):
    """Unit tests for PresidioClient with mocked HTTP responses."""

    def _get_client_class(self):
        """Import and return the PresidioClient class."""
        import importlib

        mod = importlib.import_module(PRESIDIO_MODULE)
        return mod.PresidioClient

    def _get_error_class(self):
        """Import and return the PresidioError exception class."""
        import importlib

        mod = importlib.import_module(PRESIDIO_MODULE)
        return mod.PresidioError

    def _make_client(self, analyzer_url="http://analyzer:5001", anonymizer_url="http://anonymizer:5002", timeout=5.0):
        """Create a PresidioClient instance with test URLs."""
        ClientClass = self._get_client_class()
        return ClientClass(
            analyzer_url=analyzer_url,
            anonymizer_url=anonymizer_url,
            timeout=timeout,
        )

    @patch(f"{PRESIDIO_MODULE}.httpx.AsyncClient")
    def test_analyze_returns_entities(self, mock_client_cls) -> None:
        """Analyzer endpoint returns recognized entities with correct structure."""
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {"entity_type": "PERSON", "start": 0, "end": 8, "score": 0.95},
            {"entity_type": "EMAIL_ADDRESS", "start": 20, "end": 40, "score": 0.99},
        ]
        mock_client.post = AsyncMock(return_value=mock_response)

        client = self._make_client()

        async def _test():
            result = await client.analyze("John Doe john.doe@example.com")
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0]["entity_type"], "PERSON")
            self.assertEqual(result[1]["entity_type"], "EMAIL_ADDRESS")

        run_async(_test())

    @patch(f"{PRESIDIO_MODULE}.httpx.AsyncClient")
    def test_anonymize_replaces_pii(self, mock_client_cls) -> None:
        """Anonymizer endpoint replaces PII with typed placeholders."""
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "text": "Hello <PERSON>, your email is <EMAIL_ADDRESS>.",
            "items": [
                {"operator": "replace", "entity_type": "PERSON", "start": 6, "end": 14},
                {"operator": "replace", "entity_type": "EMAIL_ADDRESS", "start": 31, "end": 46},
            ],
        }
        mock_client.post = AsyncMock(return_value=mock_response)

        client = self._make_client()

        # Pre-supply analyzer_results to isolate the anonymize step
        analyzer_results = [
            {"entity_type": "PERSON", "start": 6, "end": 14, "score": 0.95},
            {"entity_type": "EMAIL_ADDRESS", "start": 31, "end": 46, "score": 0.99},
        ]

        async def _test():
            result = await client.anonymize(
                "Hello John Doe, your email is john@example.com.",
                analyzer_results=analyzer_results,
            )
            self.assertIn("<PERSON>", result["text"])
            self.assertIn("<EMAIL_ADDRESS>", result["text"])
            self.assertNotIn("John Doe", result["text"])
            self.assertNotIn("john@example.com", result["text"])

        run_async(_test())

    @patch(f"{PRESIDIO_MODULE}.httpx.AsyncClient")
    def test_numbered_person_replacement(self, mock_client_cls) -> None:
        """Multiple PERSON entities get numbered replacements: <PERSON_1>, <PERSON_2>."""
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "text": "<PERSON_1> met <PERSON_2> at the cafe.",
            "items": [
                {"operator": "replace", "entity_type": "PERSON", "start": 0, "end": 10},
                {"operator": "replace", "entity_type": "PERSON", "start": 15, "end": 25},
            ],
        }
        mock_client.post = AsyncMock(return_value=mock_response)

        client = self._make_client()

        # Pre-supply analyzer_results to isolate the anonymize step
        analyzer_results = [
            {"entity_type": "PERSON", "start": 0, "end": 5, "score": 0.95},
            {"entity_type": "PERSON", "start": 10, "end": 13, "score": 0.92},
        ]

        async def _test():
            result = await client.anonymize(
                "Alice met Bob at the cafe.",
                analyzer_results=analyzer_results,
            )
            self.assertIn("<PERSON_1>", result["text"])
            self.assertIn("<PERSON_2>", result["text"])
            self.assertNotIn("Alice", result["text"])
            self.assertNotIn("Bob", result["text"])

        run_async(_test())

    @patch(f"{PRESIDIO_MODULE}.httpx.AsyncClient")
    def test_timeout_raises_presidio_error(self, mock_client_cls) -> None:
        """A network timeout raises PresidioError (fail closed)."""
        import httpx

        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Connection timed out"))

        client = self._make_client()
        PresidioError = self._get_error_class()

        async def _test():
            with self.assertRaises(PresidioError):
                await client.analyze("Some text with PII")

        run_async(_test())

    @patch(f"{PRESIDIO_MODULE}.httpx.AsyncClient")
    def test_http_error_raises_presidio_error(self, mock_client_cls) -> None:
        """An HTTP error status (e.g., 500) raises PresidioError (fail closed)."""
        import httpx

        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "Internal Server Error",
                request=MagicMock(),
                response=mock_response,
            )
        )
        mock_client.post = AsyncMock(return_value=mock_response)

        client = self._make_client()
        PresidioError = self._get_error_class()

        async def _test():
            with self.assertRaises(PresidioError):
                await client.analyze("Some text")

        run_async(_test())

    @patch(f"{PRESIDIO_MODULE}.httpx.AsyncClient")
    def test_malformed_response_raises_presidio_error(self, mock_client_cls) -> None:
        """A response with unexpected JSON structure raises PresidioError."""
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        # Return a malformed response (string instead of list for analyzer)
        mock_response.json.return_value = "not a valid response"
        mock_client.post = AsyncMock(return_value=mock_response)

        client = self._make_client()
        PresidioError = self._get_error_class()

        async def _test():
            with self.assertRaises(PresidioError):
                await client.analyze("Some text with PII")

        run_async(_test())


if __name__ == "__main__":
    unittest.main()
