from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).parents[1]
    / "scripts"
    / "patch_litellm_chatgpt_reasoning_effort.py"
)

UPSTREAM_FRAGMENT = '''    if effort not in ("max", "xhigh", "minimal"):
        return effort

    from litellm.utils import get_model_info
'''


class PatchLiteLLMChatGPTReasoningEffortTests(unittest.TestCase):
    def test_preserves_explicit_chatgpt_subscription_effort(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "utils.py"
            target.write_text(UPSTREAM_FRAGMENT, encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), str(target)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            patched = target.read_text(encoding="utf-8")
            self.assertIn('model.startswith("chatgpt/")', patched)
            self.assertIn('custom_llm_provider == "chatgpt"', patched)
            self.assertIn('effort in ("max", "xhigh")', patched)
            self.assertIn("return effort", patched)

    def test_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "utils.py"
            target.write_text(UPSTREAM_FRAGMENT, encoding="utf-8")

            first = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), str(target)],
                check=False,
                capture_output=True,
                text=True,
            )
            second = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), str(target)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("already-patched", second.stdout)

    def test_fails_closed_when_upstream_shape_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "utils.py"
            target.write_text("unexpected upstream source", encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), str(target)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("found 0", result.stderr)


if __name__ == "__main__":
    unittest.main()
