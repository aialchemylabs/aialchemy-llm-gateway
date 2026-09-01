from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_PATH = (
    Path(__file__).parents[1] / "scripts" / "verify_claude_oauth_passthrough_contract.py"
)


def _load_contract_module():
    spec = importlib.util.spec_from_file_location(
        "verify_claude_oauth_passthrough_contract", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTRACT = _load_contract_module()


class ClaudeOAuthPassthroughContractTests(unittest.TestCase):
    """Runs the build-time contract against the real installed litellm.

    These tests are meaningful only against a litellm whose Claude OAuth
    pass-through behavior matches the reviewed 1.99.0 mechanism; a regression
    in that mechanism must make the contract raise rather than silently pass.
    """

    def test_virtual_key_not_forwarded_and_oauth_retained(self) -> None:
        CONTRACT.verify_virtual_key_not_forwarded_and_oauth_retained()

    def test_oauth_scoped_to_anthropic_only(self) -> None:
        CONTRACT.verify_oauth_scoped_to_anthropic_only()

    def test_oauth_redacted_for_logging(self) -> None:
        CONTRACT.verify_oauth_redacted_for_logging()

    def test_no_server_side_anthropic_key_required(self) -> None:
        CONTRACT.verify_no_server_side_anthropic_key_required()

    def test_normal_authorization_used_for_auth_is_stripped(self) -> None:
        CONTRACT.verify_normal_authorization_used_for_auth_is_stripped()

    def test_main_runs_all_assertions(self) -> None:
        # Should not raise when the installed litellm honors the contract.
        CONTRACT.main()

    def test_fails_closed_if_oauth_gate_regresses(self) -> None:
        # If is_anthropic_oauth_key stops recognizing sk-ant-oat tokens, the
        # subscription flow would silently break; the contract must fail closed.
        with mock.patch.object(CONTRACT, "is_anthropic_oauth_key", return_value=False):
            with self.assertRaises(RuntimeError):
                CONTRACT.verify_no_server_side_anthropic_key_required()

    def test_fails_closed_if_oauth_leaks_to_logs(self) -> None:
        # If redaction stops masking credential headers, the OAuth token would
        # leak into logging; the contract must fail closed.
        with mock.patch.object(
            CONTRACT, "redact_credential_headers", side_effect=lambda headers: dict(headers)
        ):
            with self.assertRaises(RuntimeError):
                CONTRACT.verify_oauth_redacted_for_logging()


if __name__ == "__main__":
    unittest.main()
