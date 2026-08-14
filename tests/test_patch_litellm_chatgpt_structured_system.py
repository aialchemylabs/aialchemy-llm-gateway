from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "patch_litellm_chatgpt_structured_system.py"
SPEC = importlib.util.spec_from_file_location("patch_litellm_chatgpt_structured_system", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
PATCH_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH_MODULE)


class PatchLiteLLMChatGPTStructuredSystemTests(unittest.TestCase):
    def test_moves_structured_system_text_into_responses_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "transformation.py"
            target.write_text(f"before\n{PATCH_MODULE.OLD_BLOCK}\nafter\n", encoding="utf-8")

            self.assertEqual(PATCH_MODULE.patch_file(target), "patched")
            patched = target.read_text(encoding="utf-8")
            self.assertNotIn(PATCH_MODULE.OLD_BLOCK, patched)
            self.assertIn(PATCH_MODULE.NEW_BLOCK, patched)
            self.assertIn('item.get("role") == "system"', patched)
            self.assertIn('request["instructions"] = structured_instructions', patched)

    def test_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "transformation.py"
            target.write_text(PATCH_MODULE.NEW_BLOCK, encoding="utf-8")

            self.assertEqual(PATCH_MODULE.patch_file(target), "already-patched")

    def test_fails_closed_when_upstream_shape_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "transformation.py"
            target.write_text("unexpected upstream source", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "found 0"):
                PATCH_MODULE.patch_file(target)


if __name__ == "__main__":
    unittest.main()
