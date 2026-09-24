"""Alembic migration environment; URL is supplied only at runtime."""

import os

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from tracecause.storage.schema import Base

target_metadata = Base.metadata


def run_migrations() -> None:
    supplied = context.config.attributes.get("connection")
    if supplied is not None:
        context.configure(connection=supplied, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    url = os.environ.get("TRACECAUSE_MIGRATION_URL")
    if not url:
        raise RuntimeError("TRACECAUSE_MIGRATION_URL is required for migrations")
    if context.is_offline_mode():
        context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection, target_metadata=target_metadata, compare_type=True
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run_migrations()
