"""Tests for the LiteLLM Responses guardrail wrapper.

These cover the failure mode the unit tests for the guards themselves cannot
see: the guards are tested by calling apply_guardrail() directly, so they pass
even when the wrapper never invokes them.

The upstream signature verified in the image build is:

    process_input_messages(self, data, guardrail_to_apply, litellm_logging_obj=None)

Both meaningful parameters are positional-or-keyword with no default, so LiteLLM
may pass them either way. A wrapper that reads only kwargs resolves the
guardrail to None on a positional call and skips inspection silently — the exact
bypass must-have-requirements.md §4.3 prohibits. Every invocation style is
therefore exercised here.
"""
import asyncio
import sys
import unittest
from types import ModuleType

import tests.conftest_guardrails  # noqa: F401


def run(coro):
    return asyncio.run(coro)


class RecordingGuardrail:
    """Stands in for the guardrail LiteLLM selected for the request."""

    def __init__(self):
        self.calls = []

    async def apply_guardrail(self, inputs, request_data, input_type, **kwargs):
        self.calls.append(
            {"inputs": inputs, "request_data": request_data, "input_type": input_type}
        )
        return inputs


class FakeHandler:
    """Mimics OpenAIResponsesHandler's real signature."""

    def __init__(self):
        self.upstream_calls = 0

    async def process_input_messages(
        self, data, guardrail_to_apply, litellm_logging_obj=None
    ):
        self.upstream_calls += 1
        return data


def install_fake_handler_module():
    """Register a stub handler module so the patch can bind to it."""
    module = ModuleType("litellm.llms.openai.responses.guardrail_translation.handler")
    module.OpenAIResponsesHandler = FakeHandler

    for name in (
        "litellm.llms",
        "litellm.llms.openai",
        "litellm.llms.openai.responses",
        "litellm.llms.openai.responses.guardrail_translation",
    ):
        sys.modules.setdefault(name, ModuleType(name))

    sys.modules[
        "litellm.llms.openai.responses.guardrail_translation.handler"
    ] = module
    return module


TOOL_ONLY_INPUT = [
    {"type": "function_call", "call_id": "c1", "name": "web_search"},
    {"type": "function_call_output", "call_id": "c1", "output": "untrusted text"},
]


class ResponsesPatchTestBase(unittest.TestCase):
    def setUp(self):
        install_fake_handler_module()
        # Import fresh so apply_patch binds to the stub handler.
        for mod in list(sys.modules):
            if mod == "guardrails.litellm_responses_patch":
                del sys.modules[mod]
        import guardrails.litellm_responses_patch as patch_module

        self.patch_module = patch_module
        self.handler_cls = FakeHandler
        # Clear any flag left by a previous test.
        if hasattr(self.handler_cls, patch_module._PATCH_FLAG):
            delattr(self.handler_cls, patch_module._PATCH_FLAG)
        self.original_method = FakeHandler.process_input_messages

    def tearDown(self):
        FakeHandler.process_input_messages = self.original_method
        if hasattr(self.handler_cls, self.patch_module._PATCH_FLAG):
            delattr(self.handler_cls, self.patch_module._PATCH_FLAG)


class TestPatchApplication(ResponsesPatchTestBase):
    def test_apply_patch_reports_patched(self):
        self.assertEqual(self.patch_module.apply_patch(), "patched")

    def test_apply_patch_is_idempotent(self):
        self.assertEqual(self.patch_module.apply_patch(), "patched")
        self.assertEqual(self.patch_module.apply_patch(), "already-patched")

    def test_missing_handler_fails_closed(self):
        sys.modules[
            "litellm.llms.openai.responses.guardrail_translation.handler"
        ] = ModuleType("empty")
        for mod in list(sys.modules):
            if mod == "guardrails.litellm_responses_patch":
                del sys.modules[mod]
        import guardrails.litellm_responses_patch as fresh

        with self.assertRaises(fresh.PatchError):
            fresh.apply_patch()


class TestGuardrailInvocation(ResponsesPatchTestBase):
    """The wrapper must invoke the guardrail regardless of call style."""

    def setUp(self):
        super().setUp()
        self.patch_module.apply_patch()
        self.handler = FakeHandler()
        self.guardrail = RecordingGuardrail()

    def test_positional_call_invokes_guardrail(self):
        """The regression this test exists for.

        LiteLLM's signature makes a positional call legal. A kwargs-only wrapper
        would record zero guardrail calls here while every other test passed.
        """
        data = {"input": list(TOOL_ONLY_INPUT)}

        run(self.handler.process_input_messages(data, self.guardrail))

        self.assertEqual(
            len(self.guardrail.calls),
            1,
            "guardrail was not invoked on a positional call — tool output would "
            "reach the provider uninspected",
        )

    def test_keyword_call_invokes_guardrail(self):
        data = {"input": list(TOOL_ONLY_INPUT)}

        run(
            self.handler.process_input_messages(
                data=data, guardrail_to_apply=self.guardrail
            )
        )

        self.assertEqual(len(self.guardrail.calls), 1)

    def test_mixed_call_invokes_guardrail(self):
        data = {"input": list(TOOL_ONLY_INPUT)}

        run(
            self.handler.process_input_messages(
                data, guardrail_to_apply=self.guardrail
            )
        )

        self.assertEqual(len(self.guardrail.calls), 1)

    def test_guardrail_receives_the_raw_request(self):
        """The guard reads function_call_output from request_data, so the raw
        request must be handed through untouched."""
        data = {"input": list(TOOL_ONLY_INPUT)}

        run(self.handler.process_input_messages(data, self.guardrail))

        call = self.guardrail.calls[0]
        self.assertEqual(call["input_type"], "request")
        self.assertIs(call["request_data"], data)

    def test_upstream_is_still_called_exactly_once(self):
        """No duplicate execution of the upstream implementation."""
        data = {"input": list(TOOL_ONLY_INPUT)}

        run(self.handler.process_input_messages(data, self.guardrail))

        self.assertEqual(self.handler.upstream_calls, 1)


class TestNoDuplicateOrSpuriousInvocation(ResponsesPatchTestBase):
    def setUp(self):
        super().setUp()
        self.patch_module.apply_patch()
        self.handler = FakeHandler()
        self.guardrail = RecordingGuardrail()

    def test_no_function_call_output_is_left_to_upstream(self):
        """A plain message request is already handled by upstream extraction.

        Forcing a second invocation here would double-execute the guardrail.
        """
        data = {"input": [{"role": "user", "content": "hello"}]}

        run(self.handler.process_input_messages(data, self.guardrail))

        self.assertEqual(len(self.guardrail.calls), 0)

    def test_empty_input_is_not_inspected(self):
        run(self.handler.process_input_messages({"input": []}, self.guardrail))
        self.assertEqual(len(self.guardrail.calls), 0)

    def test_missing_input_key_is_not_inspected(self):
        run(self.handler.process_input_messages({}, self.guardrail))
        self.assertEqual(len(self.guardrail.calls), 0)

    def test_no_guardrail_selected_is_a_noop(self):
        """When LiteLLM selected no guardrail there is nothing to force."""
        data = {"input": list(TOOL_ONLY_INPUT)}
        run(self.handler.process_input_messages(data, None))
        # Reaching here without raising is the assertion.

    def test_guardrail_without_apply_guardrail_fails_closed(self):
        class Useless:
            pass

        data = {"input": list(TOOL_ONLY_INPUT)}

        with self.assertRaises(self.patch_module.PatchError):
            run(self.handler.process_input_messages(data, Useless()))


if __name__ == "__main__":
    unittest.main()
