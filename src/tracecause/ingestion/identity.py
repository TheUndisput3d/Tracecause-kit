"""Versioned, scope-preserving service identity resolution."""

import hashlib
import json
import re
from dataclasses import dataclass
from uuid import UUID

from tracecause.ingestion.redaction import SENSITIVE

SAFE_IDENTITY = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
MISSING_NAMESPACE = "<missing-namespace>"
MISSING_ENVIRONMENT = "<missing-environment>"


@dataclass(frozen=True)
class AliasRule:
    namespace: str
    environment: str
    alias: str
    canonical_name: str
    source_reference: str


@dataclass(frozen=True)
class ServiceResolution:
    key: str | None
    namespace: str | None
    environment: str | None
    name: str | None
    alias_version: str
    alias_source: str | None
    unresolved: bool


class AliasRegistry:
    def __init__(self, version: str = "1.0", rules: tuple[AliasRule, ...] = ()) -> None:
        if not version or len(version) > 100:
            raise ValueError("alias version must be bounded and nonempty")
        self.version = version
        self._rules: dict[tuple[str, str, str], AliasRule] = {}
        for rule in rules:
            if (
                not all(
                    SAFE_IDENTITY.fullmatch(value) and not SENSITIVE.search(value)
                    for value in (rule.namespace, rule.environment, rule.alias, rule.canonical_name)
                )
                or not rule.source_reference
            ):
                raise ValueError("alias rule has invalid identity or missing provenance")
            key = (rule.namespace, rule.environment, rule.alias)
            if key in self._rules:
                raise ValueError("duplicate alias rule")
            self._rules[key] = rule

    def resolve(
        self,
        workspace_id: UUID,
        namespace: str | None,
        environment: str | None,
        name: str | None,
    ) -> ServiceResolution:
        cleaned = tuple(
            value
            if value is not None and SAFE_IDENTITY.fullmatch(value) and not SENSITIVE.search(value)
            else None
            for value in (namespace, environment, name)
        )
        namespace, environment, name = cleaned
        if name is None:
            return ServiceResolution(None, namespace, environment, None, self.version, None, True)
        scoped_namespace = namespace or MISSING_NAMESPACE
        scoped_environment = environment or MISSING_ENVIRONMENT
        rule = self._rules.get((scoped_namespace, scoped_environment, name))
        canonical = rule.canonical_name if rule else name
        material = json.dumps(
            [str(workspace_id), scoped_namespace, scoped_environment, canonical],
            separators=(",", ":"),
        ).encode()
        return ServiceResolution(
            hashlib.sha256(material).hexdigest(),
            namespace,
            environment,
            canonical,
            self.version,
            rule.source_reference if rule else None,
            False,
        )
