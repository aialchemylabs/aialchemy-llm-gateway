#!/usr/bin/env python3
"""Keep the ChatGPT subscription Responses transport on its native SSE path.

The ChatGPT subscription backend requires every upstream request to use real
streaming. LiteLLM's generic Responses heuristic treats newly released model
names as non-streaming when they are not yet present in its model registry and
then removes ``stream`` from the already-correct provider request. Disable that
fake-stream fallback for the ChatGPT provider only; callers that requested a
non-streaming response are still aggregated by LiteLLM's Responses bridge.
"""

from __future__ import annotations

import argparse
from importlib.util import find_spec
from pathlib import Path


OLD_BLOCK = '''class ChatGPTResponsesAPIConfig(OpenAIResponsesAPIConfig):
    def __init__(self) -> None:
        super().__init__()
        self.authenticator = Authenticator()

    @property
    def custom_llm_provider(self) -> LlmProviders:
        return LlmProviders.CHATGPT

    def validate_environment(
'''

NEW_BLOCK = '''class ChatGPTResponsesAPIConfig(OpenAIResponsesAPIConfig):
    def __init__(self) -> None:
        super().__init__()
        self.authenticator = Authenticator()

    @property
    def custom_llm_provider(self) -> LlmProviders:
        return LlmProviders.CHATGPT

    def should_fake_stream(
        self,
        model: Optional[str],
        stream: Optional[bool],
        custom_llm_provider: Optional[str] = None,
    ) -> bool:
        # The ChatGPT subscription backend is SSE-only. Its request transformer
        # always sets ``stream: true``; fake streaming would remove that field
        # for new model names that LiteLLM's registry does not recognize yet.
        return False

    def validate_environment(
'''


def installed_transformation_path() -> Path:
    spec = find_spec("litellm")
    if spec is None or spec.origin is None:
        raise RuntimeError("The installed litellm package could not be located")
    return (
        Path(spec.origin).parent
        / "llms"
        / "chatgpt"
        / "responses"
        / "transformation.py"
    )


def patch_file(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    if NEW_BLOCK in source:
        return "already-patched"
    matches = source.count(OLD_BLOCK)
    if matches != 1:
        raise RuntimeError(
            f"Expected one ChatGPT Responses class block in {path}; found {matches}"
        )
    path.write_text(source.replace(OLD_BLOCK, NEW_BLOCK), encoding="utf-8")
    return "patched"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
    )
    args = parser.parse_args()
    path = args.path if args.path is not None else installed_transformation_path()
    result = patch_file(path)
    print(f"{result}: {path}")


if __name__ == "__main__":
    main()
