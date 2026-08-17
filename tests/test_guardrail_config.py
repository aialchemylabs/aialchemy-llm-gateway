"""Tests for guardrail configuration loading and environment variable overrides."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch


# The guardrail config module is expected at guardrails/config.py
# Import will be resolved once the module exists; tests document the contract.
MODULE_PATH = "guardrails.config"


class GuardrailConfigTests(unittest.TestCase):
    """Verify that all configuration values load correctly and env var overrides work."""

    def _import_config(self):
        """Import the config module fresh (reloads to pick up env changes)."""
        import importlib

        mod = importlib.import_module(MODULE_PATH)
        importlib.reload(mod)
        return mod

    # --- Presidio config ---

    def test_presidio_url_default(self) -> None:
        """Config provides a sensible default Presidio base URL."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PRESIDIO_ANALYZER_URL", None)
            os.environ.pop("PRESIDIO_ANONYMIZER_URL", None)
            config = self._import_config()
            self.assertTrue(hasattr(config, "PRESIDIO_ANALYZER_URL"))
            self.assertTrue(hasattr(config, "PRESIDIO_ANONYMIZER_URL"))
            self.assertIn("http", config.PRESIDIO_ANALYZER_URL)
            self.assertIn("http", config.PRESIDIO_ANONYMIZER_URL)

    def test_presidio_url_env_override(self) -> None:
        """Environment variables override Presidio service URLs."""
        with patch.dict(
            os.environ,
            {
                "PRESIDIO_ANALYZER_URL": "http://custom-analyzer:5001",
                "PRESIDIO_ANONYMIZER_URL": "http://custom-anonymizer:5002",
            },
        ):
            config = self._import_config()
            self.assertEqual(config.PRESIDIO_ANALYZER_URL, "http://custom-analyzer:5001")
            self.assertEqual(config.PRESIDIO_ANONYMIZER_URL, "http://custom-anonymizer:5002")

    def test_presidio_timeout_default(self) -> None:
        """Presidio timeout has a finite default value."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PRESIDIO_TIMEOUT_SECONDS", None)
            config = self._import_config()
            self.assertIsInstance(config.PRESIDIO_TIMEOUT_SECONDS, (int, float))
            self.assertGreater(config.PRESIDIO_TIMEOUT_SECONDS, 0)

    def test_presidio_timeout_env_override(self) -> None:
        """PRESIDIO_TIMEOUT_SECONDS env var overrides the default."""
        with patch.dict(os.environ, {"PRESIDIO_TIMEOUT_SECONDS": "10"}):
            config = self._import_config()
            self.assertEqual(config.PRESIDIO_TIMEOUT_SECONDS, 10)

    # --- Prompt Guard config ---

    def test_prompt_guard_model_name(self) -> None:
        """Config specifies the pinned Prompt Guard model identifier."""
        config = self._import_config()
        self.assertEqual(config.PROMPT_GUARD_MODEL, "meta-llama/Llama-Prompt-Guard-2-86M")

    def test_prompt_guard_threshold_default(self) -> None:
        """Classification threshold is a finite float between 0 and 1."""
        config = self._import_config()
        self.assertIsInstance(config.PROMPT_GUARD_THRESHOLD, float)
        self.assertGreater(config.PROMPT_GUARD_THRESHOLD, 0.0)
        self.assertLessEqual(config.PROMPT_GUARD_THRESHOLD, 1.0)

    def test_prompt_guard_threshold_env_override(self) -> None:
        """PROMPT_GUARD_THRESHOLD env var overrides the default."""
        with patch.dict(os.environ, {"PROMPT_GUARD_THRESHOLD": "0.85"}):
            config = self._import_config()
            self.assertEqual(config.PROMPT_GUARD_THRESHOLD, 0.85)

    def test_chunk_size_default(self) -> None:
        """Chunk size has a finite positive default."""
        config = self._import_config()
        self.assertIsInstance(config.PROMPT_GUARD_CHUNK_SIZE, int)
        self.assertGreater(config.PROMPT_GUARD_CHUNK_SIZE, 0)

    def test_chunk_size_env_override(self) -> None:
        """PROMPT_GUARD_CHUNK_SIZE env var overrides the default."""
        with patch.dict(os.environ, {"PROMPT_GUARD_CHUNK_SIZE": "384"}):
            config = self._import_config()
            self.assertEqual(config.PROMPT_GUARD_CHUNK_SIZE, 384)

    def test_chunk_overlap_default(self) -> None:
        """Chunk overlap has a finite non-negative default less than chunk size."""
        config = self._import_config()
        self.assertIsInstance(config.PROMPT_GUARD_CHUNK_OVERLAP, int)
        self.assertGreaterEqual(config.PROMPT_GUARD_CHUNK_OVERLAP, 0)
        self.assertLess(config.PROMPT_GUARD_CHUNK_OVERLAP, config.PROMPT_GUARD_CHUNK_SIZE)

    def test_chunk_overlap_env_override(self) -> None:
        """PROMPT_GUARD_CHUNK_OVERLAP env var overrides the default."""
        with patch.dict(os.environ, {"PROMPT_GUARD_CHUNK_OVERLAP": "50"}):
            config = self._import_config()
            self.assertEqual(config.PROMPT_GUARD_CHUNK_OVERLAP, 50)

    def test_max_chunks_default(self) -> None:
        """Maximum chunk count is finite and positive."""
        config = self._import_config()
        self.assertIsInstance(config.PROMPT_GUARD_MAX_CHUNKS, int)
        self.assertGreater(config.PROMPT_GUARD_MAX_CHUNKS, 0)

    def test_max_chunks_env_override(self) -> None:
        """PROMPT_GUARD_MAX_CHUNKS env var overrides the default."""
        with patch.dict(os.environ, {"PROMPT_GUARD_MAX_CHUNKS": "20"}):
            config = self._import_config()
            self.assertEqual(config.PROMPT_GUARD_MAX_CHUNKS, 20)

    def test_prompt_guard_timeout_default(self) -> None:
        """Prompt Guard timeout has a finite positive default."""
        config = self._import_config()
        self.assertIsInstance(config.PROMPT_GUARD_TIMEOUT_SECONDS, (int, float))
        self.assertGreater(config.PROMPT_GUARD_TIMEOUT_SECONDS, 0)

    def test_prompt_guard_timeout_env_override(self) -> None:
        """PROMPT_GUARD_TIMEOUT_SECONDS env var overrides the default."""
        with patch.dict(os.environ, {"PROMPT_GUARD_TIMEOUT_SECONDS": "5"}):
            config = self._import_config()
            self.assertEqual(config.PROMPT_GUARD_TIMEOUT_SECONDS, 5)

    # --- Web tool allowlist ---

    def test_web_tool_allowlist_is_set(self) -> None:
        """A version-controlled web-tool allowlist exists and is non-empty."""
        config = self._import_config()
        self.assertIsInstance(config.WEB_TOOL_ALLOWLIST, (set, frozenset, list, tuple))
        self.assertGreater(len(config.WEB_TOOL_ALLOWLIST), 0)

    def test_web_tool_allowlist_contains_known_tools(self) -> None:
        """Allowlist includes the known web tools from the requirements."""
        config = self._import_config()
        for tool_name in ("web_search", "web_extract"):
            self.assertIn(tool_name, config.WEB_TOOL_ALLOWLIST)

    # --- Presidio entities ---

    def test_presidio_entities_includes_required(self) -> None:
        """Entity list includes the mandatory entities from the requirements."""
        config = self._import_config()
        required = {
            "PERSON",
            "EMAIL_ADDRESS",
            "PHONE_NUMBER",
            "LOCATION",
            "CREDIT_CARD",
            "IBAN_CODE",
            "AU_TFN",
            "AU_MEDICARE",
            "AU_PASSPORT",
            "IN_AADHAAR",
            "IN_PAN",
            "IN_PASSPORT",
        }
        for entity in required:
            self.assertIn(entity, config.PRESIDIO_ENTITIES, f"Missing required entity: {entity}")


if __name__ == "__main__":
    unittest.main()
