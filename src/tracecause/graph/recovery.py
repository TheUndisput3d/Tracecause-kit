"""Preserve source membership and rebuild projections from authoritative storage."""

from sqlalchemy import select

from tracecause.api.resources import Resources
from tracecause.graph.projection import (
    GraphBuildError,
    load_graph_material,
    project_material,
    snapshot_membership,
    validate_projection,
)
from tracecause.graph.schema import migrate_graph
from tracecause.storage.schema import SnapshotRow


def backfill_membership(resources: Resources) -> int:
    """Validate legacy graph membership against SQL/source hashes before recording it.

    This adds recovery metadata only. Missing or corrupt historical graphs fail
    closed; new source rows are never silently substituted for old membership.
    Run with writers stopped, before taking a backup.
    """
    count = 0
    with resources.factory() as session:
        snapshots = session.scalars(select(SnapshotRow).where(SnapshotRow.status == "READY")).all()
    for snapshot in snapshots:
        membership = snapshot.membership
        if membership is None:
            with resources.driver.session(database="neo4j") as graph:
                marker = graph.run(
                    "MATCH (s:Snapshot {id:$sid,workspace_id:$wid,episode_id:$eid}) "
                    "RETURN s.document_cutoff_ns AS cutoff",
                    sid=str(snapshot.id),
                    wid=str(snapshot.workspace_id),
                    eid=str(snapshot.episode_id),
                ).single(strict=True)
                if marker is None:
                    raise GraphBuildError("legacy snapshot projection is unavailable")
                observations = graph.run(
                    "MATCH (o:Observation {snapshot_id:$sid}) RETURN o.source_id AS id",
                    sid=str(snapshot.id),
                ).data()
                documents = graph.run(
                    "MATCH (d:Document {snapshot_id:$sid}) RETURN d.document_id AS id",
                    sid=str(snapshot.id),
                ).data()
            membership = {
                "version": 1,
                "observation_ids": sorted(row["id"] for row in observations),
                "document_ids": sorted(row["id"] for row in documents),
                "document_cutoff_ns": marker["cutoff"],
            }
        with resources.factory() as session:
            material = load_graph_material(
                session,
                snapshot.workspace_id,
                snapshot.episode_id,
                artifacts=resources.artifacts,
                membership=membership,
            )
        if material.content_hash != snapshot.content_hash:
            raise GraphBuildError("legacy source membership hash mismatch")
        validate_projection(resources.driver, snapshot.id, material, complete=True)
        if snapshot.membership is None:
            with resources.factory.begin() as session:
                row = session.scalar(
                    select(SnapshotRow).where(SnapshotRow.id == snapshot.id).with_for_update()
                )
                if (
                    row is None
                    or row.status != "READY"
                    or row.content_hash != material.content_hash
                ):
                    raise GraphBuildError("snapshot changed during recovery preparation")
                row.membership = snapshot_membership(material)
            count += 1
    return count


def rebuild_empty_graph(resources: Resources) -> int:
    """Restore exact READY IDs into an empty graph; preserve SQL publication times."""
    with resources.driver.session(database="neo4j") as graph:
        if graph.run("MATCH (n) RETURN count(n) AS total").single(strict=True)["total"]:
            raise GraphBuildError("restore requires an empty isolated graph")
    migrate_graph(resources.driver)
    with resources.factory() as session:
        snapshots = session.scalars(select(SnapshotRow).where(SnapshotRow.status == "READY")).all()
    for snapshot in snapshots:
        if snapshot.membership is None:
            raise GraphBuildError("snapshot membership missing; prepare backup before restore")
        with resources.factory() as session:
            material = load_graph_material(
                session,
                snapshot.workspace_id,
                snapshot.episode_id,
                artifacts=resources.artifacts,
                membership=snapshot.membership,
            )
        if material.content_hash != snapshot.content_hash:
            raise GraphBuildError("restored source membership hash mismatch")
        project_material(resources.driver, snapshot.id, material)
        validate_projection(resources.driver, snapshot.id, material)
        with resources.driver.session(database="neo4j") as graph:
            graph.run(
                "MATCH (s:Snapshot {id:$sid}) SET s.complete=true", sid=str(snapshot.id)
            ).consume()
        validate_projection(resources.driver, snapshot.id, material, complete=True)
    return len(snapshots)
