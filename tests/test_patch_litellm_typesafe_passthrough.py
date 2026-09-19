from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts/patch_litellm_typesafe_passthrough.py"
SPEC = importlib.util.spec_from_file_location("patch_typesafe", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)


class TypeSafeBackportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sources = {
            "proxy/proxy_server.py": "app.include_router(llm_passthrough_router)\n",
            "proxy/_types.py": 'routes = [\n        "/mistral",\n        "/milvus",\n]\n',
            "types/utils.py": 'modes = [\n            "responses",\n            "ocr",\n]\n',
            "proxy/pass_through_endpoints/llm_passthrough_endpoints.py": (
                PATCH.ROUTE_ANCHOR + '\n)\nasync def milvus_proxy_route():\n    pass\n'
            ),
            "proxy/pass_through_endpoints/success_handler.py": (
                "from datetime import datetime\nfrom typing import Final\n"
                "class Logger:\n"
                "    def normalize(self):\n        if False:\n            pass\n"
                + PATCH.LOGGING_ANCHOR + "            pass\n"
                + PATCH.PREDICATE_ANCHOR + "        pass\n"
            ),
            "model_prices_and_context_window_backup.json": '{"other/model": {"input_cost_per_token": 1}}\n',
        }
        for relative, source in self.sources.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        (self.root / PATCH.HANDLER_PATH).parent.mkdir(parents=True, exist_ok=True)

    def snapshot(self) -> dict:
        return {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

    def test_installs_complete_authenticated_upstream_route_and_prices(self) -> None:
        self.assertEqual(PATCH.patch_package(self.root), "patched")
        route = (self.root / "proxy/pass_through_endpoints/llm_passthrough_endpoints.py").read_text()
        self.assertIn(PATCH.ROUTE, route)
        self.assertIn("Depends(user_api_key_auth)", route)
        self.assertIn('methods=["GET", "POST", "PUT", "DELETE", "PATCH"]', route)
        self.assertEqual((self.root / PATCH.HANDLER_PATH).read_text(), PATCH.LOGGING_HANDLER)
        pricing = json.loads((self.root / "model_prices_and_context_window_backup.json").read_text())
        self.assertEqual(pricing["other/model"], {"input_cost_per_token": 1})
        for model in PATCH.MODEL_NAMES:
            self.assertEqual(pricing[f"typesafe/{model}"], PATCH.MODEL_PRICING)

    def test_second_run_does_not_write(self) -> None:
        PATCH.patch_package(self.root)
        before = self.snapshot()
        self.assertEqual(PATCH.patch_package(self.root), "already-patched")
        self.assertEqual(self.snapshot(), before)

    def test_changed_late_anchor_does_not_partially_patch_package(self) -> None:
        path = self.root / "proxy/pass_through_endpoints/success_handler.py"
        path.write_text(path.read_text().replace(PATCH.PREDICATE_ANCHOR, "    def renamed(self):\n"))
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            PATCH.patch_package(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_duplicate_anchor_fails_before_writes(self) -> None:
        path = self.root / "proxy/_types.py"
        path.write_text(path.read_text() * 2)
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            PATCH.patch_package(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_partial_install_is_rejected(self) -> None:
        path = self.root / "proxy/_types.py"
        old, new = PATCH.SOURCE_PATCHES["proxy/_types.py"][0]
        path.write_text(path.read_text().replace(old, new))
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "Partial TypeSafe"):
            PATCH.patch_package(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_patched_package_with_duplicate_original_block_is_rejected(self) -> None:
        PATCH.patch_package(self.root)
        path = self.root / "proxy/_types.py"
        path.write_text(path.read_text() + self.sources["proxy/_types.py"])
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "duplicate original"):
            PATCH.patch_package(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_conflicting_native_handler_is_not_overwritten(self) -> None:
        path = self.root / PATCH.HANDLER_PATH
        path.write_text("# newer upstream implementation\n")
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "Unexpected existing TypeSafe handler"):
            PATCH.patch_package(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_conflicting_native_price_is_not_overwritten(self) -> None:
        path = self.root / "model_prices_and_context_window_backup.json"
        pricing = json.loads(path.read_text())
        pricing["typesafe/jev-latest"] = {**PATCH.MODEL_PRICING, "input_cost_per_token": 1}
        path.write_text(json.dumps(pricing))
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "Unexpected existing pricing"):
            PATCH.patch_package(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_future_lazy_mount_layout_requires_review(self) -> None:
        (self.root / "proxy/proxy_server.py").write_text("# now mounted lazily\n")
        with self.assertRaisesRegex(RuntimeError, "eagerly mounted"):
            PATCH.patch_package(self.root)

    def test_changed_type_safe_route_does_not_get_duplicate_route(self) -> None:
        path = self.root / "proxy/pass_through_endpoints/llm_passthrough_endpoints.py"
        path.write_text("async def typesafe_proxy_route():\n    pass\n" + path.read_text())
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "Unexpected existing TypeSafe route"):
            PATCH.patch_package(self.root)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
