#!/usr/bin/env python3
"""Route LiteLLM ChatGPT completion calls through its Responses bridge.

Claude Code sends Anthropic Messages requests. LiteLLM translates those into
chat-completion-shaped messages before provider dispatch. The ChatGPT
subscription backend accepts the Responses API and rejects system-role input
items, so the completion-to-Responses bridge must run first to move the system
prompt into the Responses ``instructions`` field.
"""

from __future__ import annotations

import argparse
from importlib.util import find_spec
from pathlib import Path


# LiteLLM 1.99.0 moved this logic into `responses_api_bridge_check()` in
# litellm/main.py and annotates the local as `dict[str, object]` (it was
# `dict[str, Any]` in 1.97.0). The anchor below tracks the 1.99.0 source.
OLD_BLOCK = '''    model_info: dict[str, object] = {}

    # Global flag: route ALL OpenAI chat completions through Responses API.'''

NEW_BLOCK = '''    model_info: dict[str, object] = {}

    # The ChatGPT subscription transport is Responses-only. Completion-shaped
    # callers (including the Anthropic Messages proxy) must enter LiteLLM's
    # bridge so system prompts become Responses ``instructions`` rather than
    # unsupported system-role input items.
    if custom_llm_provider == "chatgpt":
        model = model.replace("responses/", "")
        model_info["mode"] = "responses"
        return model_info, model

    # Global flag: route ALL OpenAI chat completions through Responses API.'''


def installed_main_path() -> Path:
    spec = find_spec("litellm")
    if spec is None or spec.origin is None:
        raise RuntimeError("The installed litellm package could not be located")
    return Path(spec.origin).parent / "main.py"


def patch_file(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    if NEW_BLOCK in source:
        return "already-patched"
    matches = source.count(OLD_BLOCK)
    if matches != 1:
        raise RuntimeError(f"Expected one LiteLLM Responses bridge block in {path}; found {matches}")
    path.write_text(source.replace(OLD_BLOCK, NEW_BLOCK), encoding="utf-8")
    return "patched"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path, default=installed_main_path())
    args = parser.parse_args()
    result = patch_file(args.path)
    print(f"{result}: {args.path}")


if __name__ == "__main__":
    main()
