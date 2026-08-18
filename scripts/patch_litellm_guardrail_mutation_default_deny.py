#!/usr/bin/env python3
"""Default-deny client attempts to modify LiteLLM guardrails.

LiteLLM 1.97.0 permits guardrail modification when a virtual key has no team,
and also permits teams unless they explicitly opt out. Production policy must
be immutable to ordinary clients, so mutation now requires the team's explicit
``metadata.guardrails.modify_guardrails: true`` permission.
"""

from __future__ import annotations

import argparse
from importlib.util import find_spec
from pathlib import Path


OLD_BLOCK = '''def can_modify_guardrails(team_obj: LiteLLM_TeamTable | None) -> bool:
    if team_obj is None:
        return True

    team_metadata: Final = team_obj.metadata or {}

    if team_metadata.get("guardrails", None) is not None and isinstance(team_metadata.get("guardrails"), dict):
        if team_metadata.get("guardrails", {}).get("modify_guardrails", None) is False:
            return False

    return True
'''

NEW_BLOCK = '''def can_modify_guardrails(team_obj: LiteLLM_TeamTable | None) -> bool:
    # Client-controlled guardrail fields can weaken mandatory server policy.
    # Require an explicit team permission; missing team/config is deny-by-default.
    if team_obj is None:
        return False

    team_metadata: Final = team_obj.metadata or {}
    guardrail_permissions: Final = team_metadata.get("guardrails")
    if not isinstance(guardrail_permissions, dict):
        return False

    return guardrail_permissions.get("modify_guardrails") is True
'''


def installed_helper_path() -> Path:
    spec = find_spec("litellm")
    if spec is None or spec.origin is None:
        raise RuntimeError("The installed litellm package could not be located")
    return (
        Path(spec.origin).parent
        / "proxy"
        / "guardrails"
        / "guardrail_helpers.py"
    )


def patch_file(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    if source.count(NEW_BLOCK) == 1:
        return "already-patched"
    matches = source.count(OLD_BLOCK)
    if matches != 1:
        raise RuntimeError(
            f"Expected one guardrail permission helper in {path}; found {matches}"
        )
    path.write_text(source.replace(OLD_BLOCK, NEW_BLOCK), encoding="utf-8")
    return "patched"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path)
    args = parser.parse_args()
    path = args.path if args.path is not None else installed_helper_path()
    result = patch_file(path)
    print(f"{result}: {path}")


if __name__ == "__main__":
    main()
