from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).parents[1]
    / "scripts"
    / "patch_litellm_responses_bridge_sse_aggregation.py"
)


class PatchLiteLLMResponsesBridgeSSEAggregationTests(unittest.TestCase):
    def _load_patch_module(self):
        spec = importlib.util.spec_from_file_location(
            "patch_litellm_responses_bridge_sse_aggregation",
            SCRIPT_PATH,
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_recovers_completed_output_items_for_sync_and_async_bridges(self) -> None:
        patch_module = self._load_patch_module()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "handler.py"
            target.write_text(
                "before\n"
                + patch_module.OLD_SYNC_BLOCK
                + "\nbetween\n"
                + patch_module.OLD_ASYNC_BLOCK
                + "\nafter\n",
                encoding="utf-8",
            )

            self.assertEqual(patch_module.patch_file(target), "patched")
            patched = target.read_text(encoding="utf-8")
            self.assertIn("_record_completed_output_item", patched)
            self.assertEqual(
                patched.count("self._record_completed_output_item(event, output_items)"),
                2,
            )
            self.assertEqual(
                patched.count("response.output = [item for _, item in sorted(output_items.items())]"),
                2,
            )
            self.assertNotIn("for _ in stream_iter:\n            pass", patched)

    def test_is_idempotent(self) -> None:
        patch_module = self._load_patch_module()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "handler.py"
            target.write_text(
                patch_module.NEW_SYNC_BLOCK + "\n" + patch_module.NEW_ASYNC_BLOCK,
                encoding="utf-8",
            )

            self.assertEqual(patch_module.patch_file(target), "already-patched")

    def test_fails_closed_when_upstream_shape_changes(self) -> None:
        patch_module = self._load_patch_module()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "handler.py"
            target.write_text("unexpected upstream source", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "sync block.*found 0"):
                patch_module.patch_file(target)


if __name__ == "__main__":
    unittest.main()
