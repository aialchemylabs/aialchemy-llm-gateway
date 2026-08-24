from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "patch_litellm_workos_custom_auth.py"


class PatchLiteLLMWorkOSCustomAuthTests(unittest.TestCase):
    def _module(self):
        spec = importlib.util.spec_from_file_location("patch_litellm_workos_custom_auth", SCRIPT_PATH)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _write_tree(root: Path, module) -> None:
        files = {
            "_types.py": (
                module.TYPE_MARKER_OLD
                + module.TYPE_VALIDATOR_OLD
                + module.PUBLIC_ROUTES_OLD
            ),
            "auth/user_api_key_auth.py": module.COMMON_CHECK_OLD,
            "response_api_endpoints/endpoints.py": module.RESPONSES_CHECK_OLD,
            "_experimental/mcp_server/auth/user_api_key_auth_mcp.py": (
                module.MCP_ROUTE_OLD
                + module.MCP_BRANCH_OLD
                + module.MCP_SCRUB_OLD
                + module.MCP_CHALLENGE_OLD
            ),
            "_experimental/mcp_server/discoverable_endpoints.py": (
                module.DISCOVERY_IMPORT_OLD
                + module.DISCOVERY_HELPER_OLD
                + module.DISCOVERY_NAMED_OLD
                + module.DISCOVERY_AGGREGATE_OLD
            ),
        }
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def test_patches_every_fail_closed_contract_and_is_idempotent(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_tree(root, module)
            self.assertEqual(module.patch_tree(root), "patched")
            self.assertEqual(module.patch_tree(root), "already-patched")

            self.assertIn(module.TYPE_MARKER_NEW, (root / "_types.py").read_text())
            self.assertIn(module.TYPE_VALIDATOR_NEW, (root / "_types.py").read_text())
            self.assertIn(module.PUBLIC_ROUTES_NEW, (root / "_types.py").read_text())
            self.assertIn(
                module.COMMON_CHECK_NEW,
                (root / "auth/user_api_key_auth.py").read_text(),
            )
            self.assertIn(
                module.RESPONSES_CHECK_NEW,
                (root / "response_api_endpoints/endpoints.py").read_text(),
            )
            mcp = (root / "_experimental/mcp_server/auth/user_api_key_auth_mcp.py").read_text()
            self.assertIn(module.MCP_ROUTE_NEW, mcp)
            self.assertIn(module.MCP_BRANCH_NEW, mcp)
            self.assertIn(module.MCP_SCRUB_NEW, mcp)
            self.assertIn(module.MCP_CHALLENGE_NEW, mcp)
            discovery = (root / "_experimental/mcp_server/discoverable_endpoints.py").read_text()
            self.assertIn(module.DISCOVERY_HELPER_NEW, discovery)
            self.assertIn(module.DISCOVERY_NAMED_NEW, discovery)
            self.assertIn(module.DISCOVERY_AGGREGATE_NEW, discovery)

    def test_fails_closed_when_upstream_shape_changes(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_tree(root, module)
            (root / "auth/user_api_key_auth.py").write_text("changed upstream", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "found 0"):
                module.patch_tree(root)


if __name__ == "__main__":
    unittest.main()
