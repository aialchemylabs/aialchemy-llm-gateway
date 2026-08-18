#!/usr/bin/env python3
"""Preserve structured-output fields for the ChatGPT Responses provider.

LiteLLM 1.97.0's completion bridge correctly maps Chat Completions
``response_format`` to Responses ``text.format``. The ChatGPT provider then
drops that field from its final allowlist, so schema-constrained Hindsight
requests silently become unconstrained. Keep the supported ``text`` field
through dispatch. The subscription backend rejects ``max_output_tokens``, so
that field must remain excluded.
"""

from __future__ import annotations

import argparse
from importlib.util import find_spec
from pathlib import Path


OLD_BLOCK = '''            "reasoning",
            "previous_response_id",
            "truncation",
'''

NEW_BLOCK = '''            "reasoning",
            "previous_response_id",
            "truncation",
            "text",
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
            f"Expected one ChatGPT Responses allowlist block in {path}; found {matches}"
        )
    path.write_text(source.replace(OLD_BLOCK, NEW_BLOCK), encoding="utf-8")
    return "patched"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path)
    args = parser.parse_args()
    path = args.path if args.path is not None else installed_transformation_path()
    result = patch_file(path)
    print(f"{result}: {path}")


if __name__ == "__main__":
    main()
