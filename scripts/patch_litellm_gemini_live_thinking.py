#!/usr/bin/env python3
"""Supply the required thinking level for Gemini 3.8 Live Extended Thinking.

LiteLLM 1.101.0 omits thinkingConfig from its Gemini Live setup, which Google
rejects for this exact model. Default its native Realtime adapter to MEDIUM
without adding thinking configuration to other Live models (which reject it).

Protocol reference: https://ai.google.dev/gemini-api/docs/live-api/thinking
"""

from __future__ import annotations

import argparse
from importlib.util import find_spec
from pathlib import Path


OLD_FINALIZER = '''    def _finalize_gemini_live_setup(model: str, setup: dict[str, Any]) -> dict[str, Any]:
        generation_config: Final = setup.get("generationConfig")
        if isinstance(generation_config, dict):
            modalities: Final = generation_config.get("responseModalities")'''

NEW_FINALIZER = '''    def _finalize_gemini_live_setup(model: str, setup: dict[str, Any]) -> dict[str, Any]:
        # Gemini 3.8 Live Extended Thinking requires an explicit thinking level.
        # Other Live models can reject thinkingConfig, so keep the match exact.
        if model == "gemini-3.8-live-extended-thinking":
            setup.setdefault("generationConfig", {}).setdefault(
                "thinkingConfig", {"thinkingLevel": "MEDIUM"}
            )
        generation_config: Final = setup.get("generationConfig")
        if isinstance(generation_config, dict):
            modalities: Final = generation_config.get("responseModalities")'''

OLD_INITIAL_SETUP = '''                "setup": setup_config,
'''

NEW_INITIAL_SETUP = '''                "setup": self._finalize_gemini_live_setup(model, setup_config),
'''

REPLACEMENTS = (
    (OLD_FINALIZER, NEW_FINALIZER),
    (OLD_INITIAL_SETUP, NEW_INITIAL_SETUP),
)


def installed_transformation_path() -> Path:
    spec = find_spec("litellm")
    if spec is None or spec.origin is None:
        raise RuntimeError("The installed litellm package could not be located")
    return Path(spec.origin).parent / "llms" / "gemini" / "realtime" / "transformation.py"


def patch_file(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    if all(source.count(new) == 1 for _, new in REPLACEMENTS):
        return "already-patched"
    # Validate every anchor before writing, including partial-patch detection.
    for old, new in REPLACEMENTS:
        matches = source.count(old)
        if matches != 1 or new in source:
            raise RuntimeError(
                f"Expected one unpatched Gemini Live setup block in {path}; found {matches}"
            )
    for old, new in REPLACEMENTS:
        source = source.replace(old, new)
    path.write_text(source, encoding="utf-8")
    return "patched"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path)
    args = parser.parse_args()
    path = args.path if args.path is not None else installed_transformation_path()
    print(f"{patch_file(path)}: {path}")


if __name__ == "__main__":
    main()
