#!/usr/bin/env python3
"""Preserve completed SSE items in LiteLLM's Responses-to-chat bridge.

The ChatGPT subscription Responses endpoint is SSE-only. Its
``response.completed`` event can contain an empty ``output`` list even though
the same stream emitted complete ``response.output_item.done`` events. LiteLLM
1.97.0 consumes those events when a Chat Completions caller requested a
non-streaming response, but then transforms only the empty completed response.

Recover only fully completed output items, keyed by their output index. Delta
events are deliberately ignored so partial provider output can never be
released as a successful completion.
"""

from __future__ import annotations

import argparse
from importlib.util import find_spec
from pathlib import Path


OLD_SYNC_BLOCK = '''    def _collect_response_from_stream(self, stream_iter: Any) -> "ResponsesAPIResponse":
        for _ in stream_iter:
            pass

        completed: Final = getattr(stream_iter, "completed_response", None)
        response_obj: Final = getattr(completed, "response", None) if completed else None
        if response_obj is None:
            raise ValueError("Stream ended without a completed response")

        hidden_params: Final = getattr(stream_iter, "_hidden_params", None)
        response: Final = self._coerce_response_object(response_obj, hidden_params)
        if not isinstance(response, ResponsesAPIResponse):
            raise ValueError("Stream completed response is invalid")
        return response
'''

NEW_SYNC_BLOCK = '''    @staticmethod
    def _record_completed_output_item(event: Any, output_items: dict[int, Any]) -> None:
        event_type = getattr(event, "type", None)
        event_type = getattr(event_type, "value", event_type)
        if event_type != "response.output_item.done":
            return

        output_index = getattr(event, "output_index", None)
        item = getattr(event, "item", None)
        if not isinstance(output_index, int) or item is None:
            return
        output_items[output_index] = item

    def _collect_response_from_stream(self, stream_iter: Any) -> "ResponsesAPIResponse":
        output_items: dict[int, Any] = {}
        for event in stream_iter:
            self._record_completed_output_item(event, output_items)

        completed: Final = getattr(stream_iter, "completed_response", None)
        response_obj: Final = getattr(completed, "response", None) if completed else None
        if response_obj is None:
            raise ValueError("Stream ended without a completed response")

        hidden_params: Final = getattr(stream_iter, "_hidden_params", None)
        response: Final = self._coerce_response_object(response_obj, hidden_params)
        if not isinstance(response, ResponsesAPIResponse):
            raise ValueError("Stream completed response is invalid")
        if not response.output and output_items:
            response.output = [item for _, item in sorted(output_items.items())]
        return response
'''

OLD_ASYNC_BLOCK = '''    async def _collect_response_from_stream_async(self, stream_iter: Any) -> "ResponsesAPIResponse":
        async for _ in stream_iter:
            pass

        completed: Final = getattr(stream_iter, "completed_response", None)
        response_obj: Final = getattr(completed, "response", None) if completed else None
        if response_obj is None:
            raise ValueError("Stream ended without a completed response")

        hidden_params: Final = getattr(stream_iter, "_hidden_params", None)
        response: Final = self._coerce_response_object(response_obj, hidden_params)
        if not isinstance(response, ResponsesAPIResponse):
            raise ValueError("Stream completed response is invalid")
        return response
'''

NEW_ASYNC_BLOCK = '''    async def _collect_response_from_stream_async(self, stream_iter: Any) -> "ResponsesAPIResponse":
        output_items: dict[int, Any] = {}
        async for event in stream_iter:
            self._record_completed_output_item(event, output_items)

        completed: Final = getattr(stream_iter, "completed_response", None)
        response_obj: Final = getattr(completed, "response", None) if completed else None
        if response_obj is None:
            raise ValueError("Stream ended without a completed response")

        hidden_params: Final = getattr(stream_iter, "_hidden_params", None)
        response: Final = self._coerce_response_object(response_obj, hidden_params)
        if not isinstance(response, ResponsesAPIResponse):
            raise ValueError("Stream completed response is invalid")
        if not response.output and output_items:
            response.output = [item for _, item in sorted(output_items.items())]
        return response
'''


def installed_handler_path() -> Path:
    spec = find_spec("litellm")
    if spec is None or spec.origin is None:
        raise RuntimeError("The installed litellm package could not be located")
    return (
        Path(spec.origin).parent
        / "completion_extras"
        / "litellm_responses_transformation"
        / "handler.py"
    )


def patch_file(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    if NEW_SYNC_BLOCK in source and NEW_ASYNC_BLOCK in source:
        return "already-patched"

    sync_matches = source.count(OLD_SYNC_BLOCK)
    if sync_matches != 1:
        raise RuntimeError(
            f"Expected one Responses bridge sync block in {path}; found {sync_matches}"
        )
    async_matches = source.count(OLD_ASYNC_BLOCK)
    if async_matches != 1:
        raise RuntimeError(
            f"Expected one Responses bridge async block in {path}; found {async_matches}"
        )

    patched = source.replace(OLD_SYNC_BLOCK, NEW_SYNC_BLOCK)
    patched = patched.replace(OLD_ASYNC_BLOCK, NEW_ASYNC_BLOCK)
    path.write_text(patched, encoding="utf-8")
    return "patched"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path)
    args = parser.parse_args()
    path = args.path if args.path is not None else installed_handler_path()
    result = patch_file(path)
    print(f"{result}: {path}")


if __name__ == "__main__":
    main()
