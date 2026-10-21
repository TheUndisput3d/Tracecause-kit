"""Long operational history reads retain snapshots and fail closed on lost leases."""

import time
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import delete, select
from tests.integration.test_api import api, ready_episode  # noqa: F401
from tests.integration.test_persistence_jobs import (  # noqa: F401
    connection_url,
    factory,
    test_database_name,
)

from tracecause.graph.errors import GraphReadError
from tracecause.graph.maintenance import reader_lease
from tracecause.storage.postgres import DEFAULT_WORKSPACE_ID
from tracecause.storage.schema import ReaderLeaseRow


def test_renewable_reader_lease_retains_scope_and_rejects_loss(api):  # noqa: F811
    client, resources = api
    manifest, _, episode, _ = ready_episode(client, resources)
    episode_id, snapshot_id = UUID(manifest["episode_id"]), UUID(episode["active_snapshot_id"])
    with reader_lease(
        resources.factory, DEFAULT_WORKSPACE_ID, episode_id, snapshot_id, seconds=1, renew=True
    ):
        time.sleep(1.15)
        with resources.factory() as session:
            lease = session.scalar(
                select(ReaderLeaseRow).where(ReaderLeaseRow.snapshot_id == snapshot_id)
            )
            assert lease is not None and lease.expires_at > datetime.now(UTC)
    with pytest.raises(GraphReadError, match="lease expired"):
        with reader_lease(
            resources.factory, DEFAULT_WORKSPACE_ID, episode_id, snapshot_id, seconds=1, renew=True
        ):
            with resources.factory.begin() as session:
                session.execute(
                    delete(ReaderLeaseRow).where(ReaderLeaseRow.snapshot_id == snapshot_id)
                )
    with resources.factory() as session:
        assert (
            session.scalar(
                select(ReaderLeaseRow.id).where(ReaderLeaseRow.snapshot_id == snapshot_id)
            )
            is None
        )
