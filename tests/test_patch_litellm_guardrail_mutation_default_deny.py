from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).parents[1]
    / "scripts"
    / "patch_litellm_guardrail_mutation_default_deny.py"
)


class PatchLiteLLMGuardrailMutationDefaultDenyTests(unittest.TestCase):
    def _load_patch_module(self):
        spec = importlib.util.spec_from_file_location(
            "patch_litellm_guardrail_mutation_default_deny",
            SCRIPT_PATH,
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_guardrail_mutation_requires_explicit_team_permission(self) -> None:
        patch_module = self._load_patch_module()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "guardrail_helpers.py"
            target.write_text(patch_module.OLD_BLOCK, encoding="utf-8")

            self.assertEqual(patch_module.patch_file(target), "patched")
            patched = target.read_text(encoding="utf-8")
            self.assertIn(patch_module.NEW_BLOCK, patched)
            self.assertNotIn(patch_module.OLD_BLOCK, patched)

    def test_is_idempotent(self) -> None:
        patch_module = self._load_patch_module()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "guardrail_helpers.py"
            target.write_text(patch_module.NEW_BLOCK, encoding="utf-8")

            self.assertEqual(patch_module.patch_file(target), "already-patched")

    def test_fails_closed_when_upstream_shape_changes(self) -> None:
        patch_module = self._load_patch_module()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "guardrail_helpers.py"
            target.write_text("unexpected upstream source", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "found 0"):
                patch_module.patch_file(target)


if __name__ == "__main__":
    unittest.main()
