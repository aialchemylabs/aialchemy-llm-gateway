#!/usr/bin/env python3
"""Preserve explicit reasoning effort for ChatGPT subscription routes.

LiteLLM normalizes ``xhigh`` and ``max`` down when its static model registry
does not recognize a model name. The gateway intentionally exposes a dynamic
``chatgpt/*`` route, so newly released models can be valid before that registry
is updated. Preserve the caller's explicit effort for this provider and let the
ChatGPT backend return a clear validation error if a particular model does not
support it instead of silently reducing requested reasoning.
"""

from __future__ import annotations

import argparse
from importlib.util import find_spec
from pathlib import Path


OLD_BLOCK = '''    if effort not in ("max", "xhigh", "minimal"):
        return effort

    from litellm.utils import get_model_info
'''

NEW_BLOCK = '''    if effort not in ("max", "xhigh", "minimal"):
        return effort

    # Dynamic ChatGPT subscription routes can expose models before LiteLLM's
    # static capability registry knows their names. Preserve explicit high-end
    # effort and let the SSE-only upstream validate support rather than silently
    # reducing xhigh/max to high.
    is_chatgpt_subscription = (
        custom_llm_provider == "chatgpt" or model.startswith("chatgpt/")
    )
    if is_chatgpt_subscription and effort in ("max", "xhigh"):
        return effort

    from litellm.utils import get_model_info
'''


def installed_utils_path() -> Path:
    spec = find_spec("litellm")
    if spec is None or spec.origin is None:
        raise RuntimeError("The installed litellm package could not be located")
    return (
        Path(spec.origin).parent
        / "llms"
        / "anthropic"
        / "experimental_pass_through"
        / "utils.py"
    )


def patch_file(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    if NEW_BLOCK in source:
        return "already-patched"
    matches = source.count(OLD_BLOCK)
    if matches != 1:
        raise RuntimeError(
            f"Expected one Anthropic effort normalization block in {path}; found {matches}"
        )
    path.write_text(source.replace(OLD_BLOCK, NEW_BLOCK), encoding="utf-8")
    return "patched"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path)
    args = parser.parse_args()
    path = args.path if args.path is not None else installed_utils_path()
    result = patch_file(path)
    print(f"{result}: {path}")


if __name__ == "__main__":
    main()
