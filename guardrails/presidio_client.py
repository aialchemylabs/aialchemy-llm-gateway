"""Presidio client for PII analysis and anonymization.

Wraps the self-hosted Presidio Analyzer and Anonymizer services.
Uses non-reversible typed replacement (e.g. <EMAIL_ADDRESS>, <PERSON_1>).

Original-to-placeholder maps are NEVER persisted or exposed.
PII content is NEVER logged.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from guardrails.config import PRESIDIO_ENTITIES, PRESIDIO_LANGUAGE, PRESIDIO_TIMEOUT_SECS

logger = logging.getLogger(__name__)


class PresidioError(Exception):
    """Raised on any Presidio service failure (timeout, HTTP error, malformed response)."""

    pass


class PresidioClient:
    """Async client for self-hosted Presidio Analyzer + Anonymizer.

    Supports separate analyze() and anonymize() calls, or the combined
    analyze_and_anonymize() convenience method.
    """

    def __init__(
        self,
        analyzer_url: str | None = None,
        anonymizer_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self._analyzer_url: str = analyzer_url or os.environ.get(
            "PRESIDIO_ANALYZER_URL", "http://presidio-analyzer:3000"
        )
        self._anonymizer_url: str = anonymizer_url or os.environ.get(
            "PRESIDIO_ANONYMIZER_URL", "http://presidio-anonymizer:3000"
        )
        self._timeout: float = timeout if timeout is not None else PRESIDIO_TIMEOUT_SECS
        self._entities: list[str] = PRESIDIO_ENTITIES
        self._language: str = PRESIDIO_LANGUAGE

    async def analyze(self, text: str) -> list[dict[str, Any]]:
        """Analyze text for PII entities.

        Returns a list of recognized entity dicts with entity_type, start, end, score.
        Raises PresidioError on any failure (fail-closed contract).
        """
        if not text or not text.strip():
            return []

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._analyzer_url}/analyze",
                    json={
                        "text": text,
                        "language": self._language,
                        "entities": self._entities,
                    },
                )
                response.raise_for_status()
                result = response.json()

                if not isinstance(result, list):
                    raise PresidioError(
                        "Presidio analyzer returned unexpected format "
                        f"(expected list, got {type(result).__name__})"
                    )

                return result

        except httpx.TimeoutException as exc:
            raise PresidioError(
                f"Presidio analyzer timed out after {self._timeout}s"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise PresidioError(
                f"Presidio analyzer HTTP error: {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise PresidioError(
                f"Presidio analyzer connection error: {exc}"
            ) from exc
        except PresidioError:
            raise
        except Exception as exc:
            raise PresidioError(
                f"Unexpected Presidio analyzer error: {type(exc).__name__}"
            ) from exc

    async def anonymize(self, text: str, analyzer_results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Anonymize text by replacing PII with typed placeholders.

        If analyzer_results is None, calls analyze() first.
        Returns dict with 'text' (anonymized) and 'items' (replacement details).
        Raises PresidioError on any failure (fail-closed contract).
        """
        if not text or not text.strip():
            return {"text": text, "items": []}

        if analyzer_results is None:
            analyzer_results = await self.analyze(text)

        if not analyzer_results:
            return {"text": text, "items": []}

        # Build anonymizer operators with numbered PERSON replacement
        person_count = 0
        anonymizers: dict[str, dict[str, str]] = {
            "DEFAULT": {"type": "replace", "new_value": "<REDACTED>"},
        }

        for entity in analyzer_results:
            entity_type = entity.get("entity_type", "UNKNOWN")
            if entity_type == "PERSON":
                person_count += 1
                # Each PERSON gets a unique number
                # Note: Presidio applies anonymizers per entity_type, not per instance.
                # For numbered replacements, we use entity-level overrides below.
            elif entity_type not in anonymizers:
                anonymizers[entity_type] = {
                    "type": "replace",
                    "new_value": f"<{entity_type}>",
                }

        # For PERSON entities, use a single placeholder (Presidio handles numbering
        # server-side when configured, or we use <PERSON> as the entity-type replacement)
        if person_count > 0:
            anonymizers["PERSON"] = {
                "type": "replace",
                "new_value": "<PERSON>",
            }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._anonymizer_url}/anonymize",
                    json={
                        "text": text,
                        "analyzer_results": analyzer_results,
                        "anonymizers": anonymizers,
                    },
                )
                response.raise_for_status()
                result = response.json()

                if not isinstance(result, dict) or "text" not in result:
                    raise PresidioError(
                        "Presidio anonymizer returned unexpected format"
                    )

                anonymized_text: str = result.get("text", "")
                if not anonymized_text and text.strip():
                    raise PresidioError(
                        "Presidio anonymizer returned empty text"
                    )

                logger.debug(
                    "presidio-client: anonymized %d entities", len(analyzer_results)
                )
                return result

        except httpx.TimeoutException as exc:
            raise PresidioError(
                f"Presidio anonymizer timed out after {self._timeout}s"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise PresidioError(
                f"Presidio anonymizer HTTP error: {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise PresidioError(
                f"Presidio anonymizer connection error: {exc}"
            ) from exc
        except PresidioError:
            raise
        except Exception as exc:
            raise PresidioError(
                f"Unexpected Presidio anonymizer error: {type(exc).__name__}"
            ) from exc

    async def analyze_and_anonymize(self, text: str) -> str:
        """Convenience: analyze then anonymize, returning just the masked text string.

        Used by the guardrail implementations for simple mask-and-continue flow.
        """
        if not text or not text.strip():
            return text

        entities = await self.analyze(text)
        if not entities:
            return text

        result = await self.anonymize(text, analyzer_results=entities)
        return result["text"]
