"""Scope-aware deterministic service mention resolution with review output."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from tracecause.documents.markdown import ParsedDocument

ALIAS_VERSION = "demo-aliases-v1"
WORD_BOUNDARY = r"(?<![\w-]){}(?![\w-])"


@dataclass(frozen=True)
class ServiceIdentity:
    service_key: str
    namespace: str
    environment: str
    name: str


@dataclass(frozen=True)
class EntityLink:
    start_char: int
    end_char: int
    mention: str
    status: Literal["accepted", "ambiguous", "unresolved"]
    service_key: str | None
    candidate_service_keys: tuple[str, ...]
    method: str
    alias_version: str


def document_matches_scope(
    document: ParsedDocument,
    services: tuple[ServiceIdentity, ...],
    *,
    require_explicit_scope: bool = False,
) -> bool:
    """Use an authored namespace/environment declaration, never a textual guess."""
    scope = document.header.service_scope
    if scope is None:
        return not require_explicit_scope
    return any(
        service.namespace == scope.namespace and service.environment == scope.environment
        for service in services
    )


def _patterns(
    document: ParsedDocument, services: tuple[ServiceIdentity, ...]
) -> dict[str, list[tuple[str, str]]]:
    patterns: dict[str, list[tuple[str, str]]] = {}
    for service in services:
        patterns.setdefault(service.name.casefold(), []).append((service.service_key, "canonical"))
    for rule in document.header.curated_service_refs:
        matching = [
            item
            for item in services
            if item.namespace == rule.namespace
            and item.environment == rule.environment
            and item.name == rule.name
        ]
        patterns.setdefault(rule.alias.casefold(), []).extend(
            (item.service_key, "curated_alias") for item in matching
        )
    return patterns


def resolve_mentions(
    document: ParsedDocument,
    services: tuple[ServiceIdentity, ...],
    *,
    include_fuzzy: bool = True,
) -> tuple[EntityLink, ...]:
    """Return accepted and reviewable ambiguous/unresolved candidates in source order."""
    scope = document.header.service_scope
    if scope is not None:
        services = tuple(
            service
            for service in services
            if service.namespace == scope.namespace and service.environment == scope.environment
        )
    patterns = _patterns(document, services)
    text = document.source_text
    candidates: list[tuple[int, int, str, list[tuple[str, str]]]] = []
    for term, matches in patterns.items():
        if not term:
            continue
        for match in re.finditer(WORD_BOUNDARY.format(re.escape(term)), text, flags=re.IGNORECASE):
            if match.start() < document.body_start:
                continue
            candidates.append(
                (match.start(), match.end(), text[match.start() : match.end()], matches)
            )
    if include_fuzzy:
        known = tuple(patterns)
        for match in re.finditer(r"(?<![\w-])[A-Za-z][A-Za-z-]{4,}(?![\w-])", text):
            if match.start() < document.body_start:
                continue
            word = match.group().casefold()
            if word in patterns:
                continue
            near = [
                term
                for term in known
                if len(term) >= 5 and SequenceMatcher(None, word, term).ratio() >= 0.9
            ]
            if near:
                guessed = [entry for term in near for entry in patterns[term]]
                candidates.append((match.start(), match.end(), match.group(), guessed))
    candidates.sort(key=lambda item: (item[0], -(item[1] - item[0]), item[2].casefold()))
    links: list[EntityLink] = []
    covered_until = document.body_start
    for start, end, mention, matches in candidates:
        if start < covered_until:
            continue
        keys = tuple(sorted(set(key for key, _ in matches)))
        method = (
            "fuzzy"
            if mention.casefold() not in patterns
            else (
                "curated_alias"
                if any(kind == "curated_alias" for _, kind in matches)
                else "canonical"
            )
        )
        if not keys:
            status: Literal["accepted", "ambiguous", "unresolved"] = "unresolved"
            key = None
        elif method == "fuzzy":
            status, key = "unresolved", None
        elif len(keys) > 1:
            status, key = "ambiguous", None
        else:
            status, key = "accepted", keys[0]
        links.append(EntityLink(start, end, mention, status, key, keys, method, ALIAS_VERSION))
        covered_until = end
    return tuple(links)


def review_report(links: tuple[EntityLink, ...]) -> dict[str, object]:
    return {
        "alias_version": ALIAS_VERSION,
        "accepted": sum(item.status == "accepted" for item in links),
        "ambiguous": sum(item.status == "ambiguous" for item in links),
        "unresolved": sum(item.status == "unresolved" for item in links),
        "review": [
            {
                "mention": item.mention,
                "start_char": item.start_char,
                "end_char": item.end_char,
                "status": item.status,
                "candidate_service_keys": item.candidate_service_keys,
                "method": item.method,
            }
            for item in links
            if item.status != "accepted"
        ],
    }
