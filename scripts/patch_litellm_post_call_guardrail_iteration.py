#!/usr/bin/env python3
"""Let LiteLLM continue past non-matching deployment guardrails.

LiteLLM 1.97.0 returns the unchanged response when a deployment-level custom
guardrail is not configured for ``post_call``. Its caller treats every
non-``None`` return as final and stops iterating callbacks, so a pre-call
guardrail registered before a post-call guardrail suppresses the latter.

Returning ``None`` for the non-matching callback preserves LiteLLM's callback
contract and lets the loop reach the applicable output guardrail.
"""

from __future__ import annotations

import argparse
from importlib.util import find_spec
from pathlib import Path


OLD_BLOCK = '''        if self.should_run_guardrail(data=request_data, event_type=GuardrailEventHooks.post_call) is not True:
            return response
'''

NEW_BLOCK = '''        if self.should_run_guardrail(data=request_data, event_type=GuardrailEventHooks.post_call) is not True:
            # ``async_post_call_success_deployment_hook`` iterates callbacks
            # until one returns a value. Non-matching guardrails must therefore
            # return None so a later post-call guardrail can inspect the output.
            return None
'''


def installed_guardrail_path() -> Path:
    spec = find_spec("litellm")
    if spec is None or spec.origin is None:
        raise RuntimeError("The installed litellm package could not be located")
    return Path(spec.origin).parent / "integrations" / "custom_guardrail.py"


def patch_file(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    if source.count(NEW_BLOCK) == 1:
        return "already-patched"
    matches = source.count(OLD_BLOCK)
    if matches != 1:
        raise RuntimeError(
            f"Expected one deployment post-call guardrail block in {path}; found {matches}"
        )
    path.write_text(source.replace(OLD_BLOCK, NEW_BLOCK), encoding="utf-8")
    return "patched"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path)
    args = parser.parse_args()
    path = args.path if args.path is not None else installed_guardrail_path()
    result = patch_file(path)
    print(f"{result}: {path}")


if __name__ == "__main__":
    main()
