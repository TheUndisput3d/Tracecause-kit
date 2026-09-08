"""Quote-verified authored assertions; historical reports remain claims."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID, uuid5

from tracecause.documents.linking import EntityLink
from tracecause.documents.markdown import ParsedDocument

EXTRACTOR_VERSION = "deterministic-v1"
ASSERTION = re.compile(r"(?m)^\*\*(Symptom|Check|Historical report):\*\*[ \t]+([^\r\n]+)")
ALLOWED_PREDICATES = frozenset({"DESCRIBES_SYMPTOM", "SUGGESTS_CHECK", "REPORTS_HISTORICAL_CAUSE"})
PREDICATE_BY_LABEL = {
    "Symptom": "DESCRIBES_SYMPTOM",
    "Check": "SUGGESTS_CHECK",
    "Historical report": "REPORTS_HISTORICAL_CAUSE",
}


class AssertionError(ValueError):
    """An assertion lacks an exact quote or allowed scope/predicate."""


@dataclass(frozen=True)
class DocumentAssertion:
    id: UUID
    document_id: UUID
    predicate: str
    quote: str
    start_char: int
    end_char: int
    extractor_version: str
    verification_status: Literal["verified"]
    subject_service_key: str | None


class AssertionExtractor(Protocol):
    def extract(
        self, document: ParsedDocument, links: tuple[EntityLink, ...]
    ) -> tuple[DocumentAssertion, ...]: ...


def validate_assertion(
    document: ParsedDocument,
    assertion: DocumentAssertion,
    allowed_service_keys: set[str],
) -> None:
    if assertion.document_id != document.document_id:
        raise AssertionError("assertion document scope differs")
    if assertion.predicate not in ALLOWED_PREDICATES:
        raise AssertionError("unsupported document predicate")
    if (
        assertion.start_char < document.body_start
        or assertion.end_char > len(document.source_text)
        or assertion.end_char <= assertion.start_char
        or document.source_text[assertion.start_char : assertion.end_char] != assertion.quote
    ):
        raise AssertionError("assertion quote does not match exact source offsets")
    if assertion.subject_service_key is not None and (
        assertion.subject_service_key not in allowed_service_keys
    ):
        raise AssertionError("assertion subject is outside graph scope")
    if (
        assertion.predicate == "REPORTS_HISTORICAL_CAUSE"
        and document.header.document_type != "historical_note"
    ):
        raise AssertionError("historical cause requires a historical note")


class DeterministicAssertionExtractor:
    def extract(
        self, document: ParsedDocument, links: tuple[EntityLink, ...]
    ) -> tuple[DocumentAssertion, ...]:
        accepted = tuple(item for item in links if item.status == "accepted")
        allowed_keys = {item.service_key for item in accepted if item.service_key is not None}
        assertions: list[DocumentAssertion] = []
        for match in ASSERTION.finditer(document.source_text, document.body_start):
            predicate = PREDICATE_BY_LABEL[match.group(1)]
            if predicate == "REPORTS_HISTORICAL_CAUSE" and (
                document.header.document_type != "historical_note"
            ):
                raise AssertionError("historical report appears outside a historical note")
            start, end = match.span(2)
            within_quote = sorted(
                {
                    item.service_key
                    for item in accepted
                    if item.service_key is not None
                    and item.start_char >= start
                    and item.end_char <= end
                }
            )
            subject = within_quote[0] if len(within_quote) == 1 else None
            assertion = DocumentAssertion(
                id=uuid5(document.document_id, f"{EXTRACTOR_VERSION}:{start}:{end}:{predicate}"),
                document_id=document.document_id,
                predicate=predicate,
                quote=match.group(2),
                start_char=start,
                end_char=end,
                extractor_version=EXTRACTOR_VERSION,
                verification_status="verified",
                subject_service_key=subject,
            )
            validate_assertion(document, assertion, allowed_keys)
            assertions.append(assertion)
        return tuple(assertions)
