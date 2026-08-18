#!/usr/bin/env python3
"""Preserve policy-selected guardrails across LiteLLM pipeline steps.

LiteLLM 1.97.0 injects each ordered pipeline step by replacing
``metadata.guardrails`` with a one-item list. The copied request is later
returned as modified data, so the final pre-call step permanently replaces the
full policy selection and suppresses post-call guardrails.
"""

from __future__ import annotations

import argparse
from importlib.util import find_spec
from pathlib import Path


OLD_COPY_BLOCK = '''        working_data = data.copy()
        if "metadata" in working_data:
            working_data["metadata"] = working_data["metadata"].copy()
'''

NEW_COPY_BLOCK = '''        working_data = data.copy()
        guardrails_were_selected = False
        selected_guardrails = None
        if "metadata" in working_data:
            working_data["metadata"] = working_data["metadata"].copy()
            guardrails_were_selected = "guardrails" in working_data["metadata"]
            selected_guardrails = working_data["metadata"].get("guardrails")
            if isinstance(selected_guardrails, list):
                selected_guardrails = selected_guardrails.copy()
'''

OLD_STEP_BLOCK = '''            ) = await PipelineExecutor._run_step(
                step=step,
                mode=mode,
                data=working_data,
                user_api_key_dict=user_api_key_dict,
                call_type=call_type,
            )
'''

NEW_STEP_BLOCK = '''            ) = await PipelineExecutor._run_step(
                step=step,
                mode=mode,
                data=working_data,
                user_api_key_dict=user_api_key_dict,
                call_type=call_type,
            )

            # _run_step temporarily narrows metadata.guardrails to the current
            # pipeline step. Restore the complete policy selection so guards
            # from other phases, especially post_call, remain active.
            working_metadata = working_data.get("metadata")
            if isinstance(working_metadata, dict):
                if guardrails_were_selected:
                    working_metadata["guardrails"] = selected_guardrails
                else:
                    working_metadata.pop("guardrails", None)
'''


def installed_executor_path() -> Path:
    spec = find_spec("litellm")
    if spec is None or spec.origin is None:
        raise RuntimeError("The installed litellm package could not be located")
    return (
        Path(spec.origin).parent
        / "proxy"
        / "policy_engine"
        / "pipeline_executor.py"
    )


def patch_file(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    if source.count(NEW_COPY_BLOCK) == 1 and source.count(NEW_STEP_BLOCK) == 1:
        return "already-patched"
    copy_matches = source.count(OLD_COPY_BLOCK)
    step_matches = source.count(OLD_STEP_BLOCK)
    if copy_matches != 1 or step_matches != 1:
        raise RuntimeError(
            "Expected one pipeline copy block and one step block in "
            f"{path}; found copy block {copy_matches}, step block {step_matches}"
        )
    source = source.replace(OLD_COPY_BLOCK, NEW_COPY_BLOCK)
    source = source.replace(OLD_STEP_BLOCK, NEW_STEP_BLOCK)
    path.write_text(source, encoding="utf-8")
    return "patched"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path)
    args = parser.parse_args()
    path = args.path if args.path is not None else installed_executor_path()
    result = patch_file(path)
    print(f"{result}: {path}")


if __name__ == "__main__":
    main()
