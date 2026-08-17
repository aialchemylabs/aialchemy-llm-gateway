#!/usr/bin/env python3
"""Preserve structured Claude Code system prompts for ChatGPT Responses.

LiteLLM's generic completion-to-Responses bridge moves string system messages
to ``instructions`` but retains structured system content as a system-role
input item. The ChatGPT subscription Responses endpoint rejects that role.
This provider-specific patch extracts text-only structured system blocks into
``instructions`` and deliberately leaves unrepresentable blocks untouched so
the request fails closed instead of silently losing prompt content.
"""

from __future__ import annotations

import argparse
from importlib.util import find_spec
from pathlib import Path


OLD_BLOCK = '''        request: Final = super().transform_responses_api_request(
            model,
            input,
            response_api_optional_request_params,
            litellm_params,
            headers,
        )
        base_instructions: Final = get_chatgpt_default_instructions()'''

NEW_BLOCK = '''        request: Final = super().transform_responses_api_request(
            model,
            input,
            response_api_optional_request_params,
            litellm_params,
            headers,
        )

        input_items = request.get("input")
        if isinstance(input_items, list):
            filtered_input_items = []
            structured_system_text = []
            for item in input_items:
                system_text_parts = None
                if isinstance(item, dict) and item.get("role") == "system":
                    content = item.get("content")
                    if isinstance(content, str):
                        system_text_parts = [content]
                    elif isinstance(content, list):
                        candidate_parts = []
                        representable = True
                        for block in content:
                            if isinstance(block, dict) and isinstance(block.get("text"), str):
                                candidate_parts.append(block["text"])
                            else:
                                representable = False
                                break
                        if representable:
                            system_text_parts = candidate_parts

                if system_text_parts is None:
                    filtered_input_items.append(item)
                else:
                    structured_system_text.extend(system_text_parts)

            request["input"] = filtered_input_items
            if structured_system_text:
                structured_instructions = "\\n\\n".join(structured_system_text)
                existing_instructions = request.get("instructions")
                if existing_instructions:
                    request["instructions"] = (
                        f"{structured_instructions}\\n\\n{existing_instructions}"
                    )
                else:
                    request["instructions"] = structured_instructions

        base_instructions: Final = get_chatgpt_default_instructions()'''


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
        raise RuntimeError(f"Expected one ChatGPT Responses request block in {path}; found {matches}")
    path.write_text(source.replace(OLD_BLOCK, NEW_BLOCK), encoding="utf-8")
    return "patched"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path, default=installed_transformation_path())
    args = parser.parse_args()
    result = patch_file(args.path)
    print(f"{result}: {args.path}")


if __name__ == "__main__":
    main()
