"""Safe parsing and rewriting of Responses ``function_call_output.output``.

The Responses API accepts either a string or a list of text content parts.  Both
the PII input guard and the web-result guard need the same strict behavior:
inspect every provider-visible string, preserve the original structure, and
fail closed on unsupported/multimodal shapes rather than flattening or skipping
content.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from guardrails.config import MAX_TOOL_OUTPUT_PARTS


_TEXT_PART_TYPES = frozenset({"text", "input_text", "output_text"})
_TEXT_PART_KEYS = frozenset({"type", "text"})


class ToolOutputShapeError(ValueError):
    """Raised when a tool output contains provider-visible data we cannot scan."""


@dataclass(frozen=True)
class ToolOutputTextPart:
    """One scannable string and its location in the original output."""

    index: int | None
    text: str
    mapping_part: bool = False


def collect_text_parts(output: Any) -> list[ToolOutputTextPart]:
    """Return every scannable string in an allowed tool-output shape.

    Empty strings are retained for shape-preserving rewrite but do not need a
    Presidio or classifier call.  Dict parts are intentionally strict: unknown
    sibling fields could contain unscanned provider-visible text.
    """

    if isinstance(output, str):
        return [ToolOutputTextPart(index=None, text=output)]

    if not isinstance(output, list):
        raise ToolOutputShapeError(
            f"unexpected function_call_output type: {type(output).__name__}"
        )
    if len(output) > MAX_TOOL_OUTPUT_PARTS:
        raise ToolOutputShapeError(
            "function_call_output exceeds the configured structured part limit"
        )

    parts: list[ToolOutputTextPart] = []
    for index, part in enumerate(output):
        if isinstance(part, str):
            parts.append(ToolOutputTextPart(index=index, text=part))
            continue

        if not isinstance(part, dict):
            raise ToolOutputShapeError(
                "non-scannable function_call_output element "
                f"at index {index}: {type(part).__name__}"
            )

        part_type = part.get("type")
        text = part.get("text")
        if (
            part_type not in _TEXT_PART_TYPES
            or not isinstance(text, str)
            or not set(part).issubset(_TEXT_PART_KEYS)
        ):
            raise ToolOutputShapeError(
                f"non-scannable structured function_call_output element at index {index}"
            )

        parts.append(
            ToolOutputTextPart(index=index, text=text, mapping_part=True)
        )

    return parts


def replace_text_parts(
    output: Any,
    parts: Sequence[ToolOutputTextPart],
    replacements: Sequence[str],
) -> Any:
    """Return ``output`` with text replacements and the same API structure."""

    if len(parts) != len(replacements):
        raise ToolOutputShapeError(
            "function_call_output rewrite cardinality mismatch"
        )

    if isinstance(output, str):
        if len(replacements) != 1:
            raise ToolOutputShapeError(
                "string function_call_output rewrite cardinality mismatch"
            )
        return replacements[0]

    if not isinstance(output, list):
        raise ToolOutputShapeError("function_call_output changed shape during rewrite")

    rewritten = list(output)
    for part, replacement in zip(parts, replacements):
        if part.index is None or part.index >= len(rewritten):
            raise ToolOutputShapeError("function_call_output rewrite index mismatch")
        if part.mapping_part:
            original_mapping = rewritten[part.index]
            if not isinstance(original_mapping, dict):
                raise ToolOutputShapeError("function_call_output changed shape during rewrite")
            updated_mapping = dict(original_mapping)
            updated_mapping["text"] = replacement
            rewritten[part.index] = updated_mapping
        else:
            rewritten[part.index] = replacement

    return rewritten


def total_utf8_bytes(parts: Sequence[ToolOutputTextPart]) -> int:
    """Return the exact UTF-8 byte count of all text-bearing parts."""

    return sum(len(part.text.encode("utf-8")) for part in parts)
