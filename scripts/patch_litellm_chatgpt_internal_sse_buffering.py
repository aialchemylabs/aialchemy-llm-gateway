#!/usr/bin/env python3
"""Buffer ChatGPT's required provider SSE for non-streaming clients.

The ChatGPT subscription backend requires ``stream: true`` on the provider
request. LiteLLM treats that provider-forced value as if the client had
requested streaming and returns the iterator directly, bypassing final-response
hooks. Keep the upstream SSE transport, but buffer and transform it whenever
the original client request did not explicitly set ``stream: true``.

LiteLLM 1.99.0 still guards fake-stream preparation with
``is_stream_request: Final = bool(stream)`` followed immediately by
``if is_stream_request and fake_stream is True:`` (now delegating the actual
rewrite to ``_prepare_fake_stream_request``) in both the sync
``response_api_handler`` and async ``async_response_api_handler`` paths, so the
anchor appears exactly twice. Because ``is_stream_request`` is annotated
``Final`` upstream, the patch drops that annotation on the anchor line so the
conditional buffering reassignment below it is a clean plain assignment rather
than a redefinition of a ``Final`` local.
"""

from __future__ import annotations

import argparse
from importlib.util import find_spec
from pathlib import Path


OLD_BLOCK = '''        is_stream_request: Final = bool(stream)
        if is_stream_request and fake_stream is True:
'''

NEW_BLOCK = '''        is_stream_request = bool(stream)
        if custom_llm_provider == "chatgpt" and not bool(
            response_api_optional_request_params.get("stream", False)
        ):
            # ChatGPT requires upstream SSE even for a non-streaming client.
            # Buffer that provider response so LiteLLM can assemble and
            # transform the complete response before returning it.
            is_stream_request = False
        if is_stream_request and fake_stream is True:
'''


def installed_handler_path() -> Path:
    spec = find_spec("litellm")
    if spec is None or spec.origin is None:
        raise RuntimeError("The installed litellm package could not be located")
    return Path(spec.origin).parent / "llms" / "custom_httpx" / "llm_http_handler.py"


def patch_file(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    if source.count(NEW_BLOCK) == 2:
        return "already-patched"
    matches = source.count(OLD_BLOCK)
    if matches != 2:
        raise RuntimeError(
            f"Expected two Responses HTTP stream blocks in {path}; found {matches}"
        )
    path.write_text(source.replace(OLD_BLOCK, NEW_BLOCK), encoding="utf-8")
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
