from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "patch_litellm_gemini_live_thinking.py"
SPEC = importlib.util.spec_from_file_location("patch_litellm_gemini_live_thinking", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
PATCH_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH_MODULE)


class PatchGeminiLiveThinkingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.target = Path(self.directory.name) / "transformation.py"

    def test_patches_both_setup_paths_and_is_idempotent(self) -> None:
        self.target.write_text("\n".join(old for old, _ in PATCH_MODULE.REPLACEMENTS))
        self.assertEqual(PATCH_MODULE.patch_file(self.target), "patched")
        patched = self.target.read_text()
        for _, new in PATCH_MODULE.REPLACEMENTS:
            self.assertIn(new, patched)
        self.assertEqual(PATCH_MODULE.patch_file(self.target), "already-patched")
        self.assertEqual(self.target.read_text(), patched)

    def test_missing_initial_setup_does_not_partially_write(self) -> None:
        source = PATCH_MODULE.OLD_FINALIZER + "\nchanged upstream serialization\n"
        self.target.write_text(source)
        with self.assertRaisesRegex(RuntimeError, "found 0"):
            PATCH_MODULE.patch_file(self.target)
        self.assertEqual(self.target.read_text(), source)

    def test_rejects_partial_patch(self) -> None:
        source = PATCH_MODULE.NEW_FINALIZER + "\n" + PATCH_MODULE.OLD_INITIAL_SETUP
        self.target.write_text(source)
        with self.assertRaises(RuntimeError):
            PATCH_MODULE.patch_file(self.target)
        self.assertEqual(self.target.read_text(), source)

    def test_rejects_ambiguous_upstream_match(self) -> None:
        source = PATCH_MODULE.OLD_FINALIZER * 2 + PATCH_MODULE.OLD_INITIAL_SETUP
        self.target.write_text(source)
        with self.assertRaisesRegex(RuntimeError, "found 2"):
            PATCH_MODULE.patch_file(self.target)
        self.assertEqual(self.target.read_text(), source)


if __name__ == "__main__":
    unittest.main()
