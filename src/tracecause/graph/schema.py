"""Versioned Neo4j schema for the snapshot-local graph projection."""

from __future__ import annotations

from pathlib import Path

from neo4j import Driver, GraphDatabase

GRAPH_SCHEMA_VERSION = "1.2"
CONSTRAINTS = {
    "tc_snapshot_id": "Snapshot",
    "tc_service_id": "Service",
    "tc_observation_id": "Observation",
    "tc_incident_id": "Incident",
    "tc_document_id": "Document",
    "tc_chunk_id": "Chunk",
    "tc_assertion_id": "Assertion",
    "tc_operational_id": "OperationalEntity",
    "tc_handoff_id": "HandoffObservation",
}
INDEXES = {
    "tc_handoff_scope_end": ("HandoffObservation", "workspace_id, episode_id, snapshot_id, end_ns"),
    "tc_operational_scope": ("OperationalEntity", "workspace_id, episode_id, snapshot_id"),
    "tc_service_scope_key": ("Service", "workspace_id, episode_id, snapshot_id, service_key"),
    "tc_observation_scope_end": ("Observation", "workspace_id, episode_id, snapshot_id, end_ns"),
    "tc_incident_scope_end": ("Incident", "workspace_id, episode_id, snapshot_id, end_ns"),
    "tc_document_scope_published_ns": (
        "Document",
        "workspace_id, episode_id, snapshot_id, published_at_ns",
    ),
    "tc_chunk_scope_document": ("Chunk", "workspace_id, episode_id, snapshot_id, document_id"),
    "tc_retrievable_scope_snapshot": ("Retrievable", "workspace_id, episode_id, snapshot_id"),
}


class GraphSchemaError(ValueError):
    """A required graph schema object is absent or incompatible."""


def driver_from_auth_file(uri: str, auth_file: Path) -> Driver:
    if (
        not uri.startswith(("bolt://", "neo4j://"))
        or auth_file.is_symlink()
        or not auth_file.is_file()
    ):
        raise GraphSchemaError("invalid graph connection configuration")
    credentials = auth_file.read_text(encoding="utf-8").strip().split("/", 1)
    if len(credentials) != 2 or not all(credentials):
        raise GraphSchemaError("invalid graph auth file")
    return GraphDatabase.driver(uri, auth=(credentials[0], credentials[1]), connection_timeout=5)


def migrate_graph(driver: Driver) -> dict[str, object]:
    driver.verify_connectivity()
    with driver.session(database="neo4j") as session:
        existing = session.run(
            "MATCH (v:TraceCauseSchema {name: $name}) RETURN v.version AS version",
            name="graph",
        ).single()
        if existing is not None and existing["version"] not in {"1.0", "1.1", GRAPH_SCHEMA_VERSION}:
            raise GraphSchemaError("graph schema version is incompatible")
        session.run(
            "CREATE CONSTRAINT tc_schema_name IF NOT EXISTS "
            "FOR (v:TraceCauseSchema) REQUIRE v.name IS UNIQUE"
        ).consume()
        for name, label in CONSTRAINTS.items():
            session.run(
                f"CREATE CONSTRAINT {name} IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE"
            ).consume()
        for name, (label, properties) in INDEXES.items():
            session.run(
                f"CREATE INDEX {name} IF NOT EXISTS FOR (n:{label}) ON "
                f"({', '.join('n.' + part.strip() for part in properties.split(','))})"
            ).consume()
        session.run(
            "MERGE (v:TraceCauseSchema {name: $name}) SET v.version = $version",
            name="graph",
            version=GRAPH_SCHEMA_VERSION,
        ).consume()
        session.run("CALL db.awaitIndexes(30)").consume()
    return audit_graph_schema(driver)


def audit_graph_schema(driver: Driver) -> dict[str, object]:
    with driver.session(database="neo4j") as session:
        constraints = {
            record["name"] for record in session.run("SHOW CONSTRAINTS YIELD name RETURN name")
        }
        indexes = {
            record["name"]: record["state"]
            for record in session.run("SHOW INDEXES YIELD name, state RETURN name, state")
        }
        version = session.run(
            "MATCH (v:TraceCauseSchema {name: $name}) RETURN v.version AS version",
            name="graph",
        ).single()
    missing_constraints = sorted((CONSTRAINTS.keys() | {"tc_schema_name"}) - constraints)
    missing_indexes = sorted(name for name in INDEXES if indexes.get(name) != "ONLINE")
    if (
        missing_constraints
        or missing_indexes
        or version is None
        or version["version"] != GRAPH_SCHEMA_VERSION
    ):
        raise GraphSchemaError("required graph constraints or scope indexes are not ready")
    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "constraints_online": len(CONSTRAINTS) + 1,
        "scope_indexes_online": len(INDEXES),
    }
