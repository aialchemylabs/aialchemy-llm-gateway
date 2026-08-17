from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).parents[1]
    / "scripts"
    / "patch_litellm_chatgpt_native_stream.py"
)

UPSTREAM_CLASS_FRAGMENT = '''class ChatGPTResponsesAPIConfig(OpenAIResponsesAPIConfig):
    def __init__(self) -> None:
        super().__init__()
        self.authenticator = Authenticator()

    @property
    def custom_llm_provider(self) -> LlmProviders:
        return LlmProviders.CHATGPT

    def validate_environment(
'''


class PatchLiteLLMChatGPTNativeStreamTests(unittest.TestCase):
    def test_disables_fake_stream_for_every_chatgpt_subscription_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "transformation.py"
            target.write_text(UPSTREAM_CLASS_FRAGMENT, encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), str(target)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            patched = target.read_text(encoding="utf-8")
            self.assertIn("def should_fake_stream(", patched)
            self.assertIn("return False", patched)
            self.assertIn("ChatGPT subscription backend is SSE-only", patched)
            self.assertIn("model: str | None", patched)
            self.assertIn("stream: bool | None", patched)
            self.assertNotIn("Optional[", patched)

    def test_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "transformation.py"
            target.write_text(UPSTREAM_CLASS_FRAGMENT, encoding="utf-8")

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
            target = Path(directory) / "transformation.py"
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
