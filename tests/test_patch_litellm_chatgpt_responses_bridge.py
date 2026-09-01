from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "patch_litellm_chatgpt_responses_bridge.py"
SPEC = importlib.util.spec_from_file_location("patch_litellm_chatgpt_responses_bridge", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
PATCH_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH_MODULE)


LITELLM_1_99_FRAGMENT = '''    model_info: dict[str, object] = {}

    # Global flag: route ALL OpenAI chat completions through Responses API.
    # Returns early with minimal model_info; callers only inspect the "mode" key.
'''


class PatchLiteLLMChatGPTResponsesBridgeTests(unittest.TestCase):
    def test_routes_chatgpt_completion_calls_through_responses_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "main.py"
            target.write_text(f"before\n{PATCH_MODULE.OLD_BLOCK}\nafter\n", encoding="utf-8")

            self.assertEqual(PATCH_MODULE.patch_file(target), "patched")
            patched = target.read_text(encoding="utf-8")
            self.assertNotIn(PATCH_MODULE.OLD_BLOCK, patched)
            self.assertIn(PATCH_MODULE.NEW_BLOCK, patched)

    def test_routes_1_99_chatgpt_calls_through_responses_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "main.py"
            target.write_text(LITELLM_1_99_FRAGMENT, encoding="utf-8")

            self.assertEqual(PATCH_MODULE.patch_file(target), "patched")
            patched = target.read_text(encoding="utf-8")
            self.assertIn('custom_llm_provider == "chatgpt"', patched)
            self.assertIn('model_info["mode"] = "responses"', patched)
            self.assertIn(
                "# Global flag: route ALL OpenAI chat completions through Responses API.",
                patched,
            )

    def test_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "main.py"
            target.write_text(PATCH_MODULE.NEW_BLOCK, encoding="utf-8")

            self.assertEqual(PATCH_MODULE.patch_file(target), "already-patched")

    def test_fails_closed_when_upstream_shape_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "main.py"
            target.write_text("unexpected upstream source", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "found 0"):
                PATCH_MODULE.patch_file(target)


if __name__ == "__main__":
    unittest.main()
