"""Process-owned database clients; model initialization belongs to the worker."""

from dataclasses import dataclass
from pathlib import Path

from neo4j import Driver, GraphDatabase
from sqlalchemy import Engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from tracecause.config.settings import RuntimeSettings
from tracecause.graph.queries import GraphReader
from tracecause.investigation.service import InvestigationService
from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.postgres import make_engine, sessions


@dataclass
class Resources:
    settings: RuntimeSettings
    factory: sessionmaker[Session]
    driver: Driver
    artifacts: ArtifactRepository
    engine: Engine | None = None

    @property
    def reader(self) -> GraphReader:
        return GraphReader(self.factory, self.driver)

    @property
    def investigations(self) -> InvestigationService:
        return InvestigationService(
            self.factory, self.driver, self.artifacts, self.artifacts.root / "embedding-cache"
        )

    @property
    def staging(self) -> Path:
        return self.settings.staging_root

    def close(self) -> None:
        self.driver.close()
        if self.engine is not None:
            self.engine.dispose()


def open_resources(settings: RuntimeSettings) -> Resources:
    if settings.pg_password is None or settings.neo4j_auth is None:
        raise ValueError("database credentials are required")
    user, separator, password = settings.neo4j_auth.get_secret_value().partition("/")
    if not separator or not user or not password:
        raise ValueError("graph credentials are invalid")
    engine = make_engine(
        URL.create(
            "postgresql+psycopg",
            username=settings.pg_user,
            password=settings.pg_password.get_secret_value(),
            host=settings.pg_host,
            port=settings.pg_port,
            database=settings.pg_database,
        )
    )
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(user, password),
        connection_timeout=3,
        connection_acquisition_timeout=3,
        max_connection_pool_size=5,
        max_transaction_retry_time=3,
    )
    return Resources(
        settings, sessions(engine), driver, ArtifactRepository(settings.artifact_root), engine
    )
