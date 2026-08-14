#!/usr/bin/env python3
"""Patch LiteLLM's Responses health probe to use canonical list input.

LiteLLM 1.95.0 passes a bare string to ``aresponses`` during model health
checks. The ChatGPT subscription adapter intentionally accepts Responses
input items only, so every otherwise-working ChatGPT deployment is reported
as unhealthy. A one-item user message list is valid for both the OpenAI
Responses API and the ChatGPT adapter.
"""

from __future__ import annotations

import argparse
from importlib.util import find_spec
from pathlib import Path


OLD_BLOCK = '''            "responses": lambda: litellm.aresponses(
                **_filter_model_params(model_params=model_params),
                input=prompt or "test",
            ),'''

NEW_BLOCK = '''            "responses": lambda: litellm.aresponses(
                **_filter_model_params(model_params=model_params),
                input=[{"role": "user", "content": prompt or "test"}],
            ),'''


def installed_helper_path() -> Path:
    spec = find_spec("litellm")
    if spec is None or spec.origin is None:
        raise RuntimeError("The installed litellm package could not be located")
    return Path(spec.origin).parent / "litellm_core_utils" / "health_check_helpers.py"


def patch_file(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    if NEW_BLOCK in source:
        return "already-patched"
    matches = source.count(OLD_BLOCK)
    if matches != 1:
        raise RuntimeError(f"Expected one LiteLLM Responses health block in {path}; found {matches}")
    path.write_text(source.replace(OLD_BLOCK, NEW_BLOCK), encoding="utf-8")
    return "patched"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path, default=installed_helper_path())
    args = parser.parse_args()
    result = patch_file(args.path)
    print(f"{result}: {args.path}")


if __name__ == "__main__":
    main()
