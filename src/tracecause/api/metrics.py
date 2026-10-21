"""Process-local HTTP metrics plus authoritative queue state; no user-text labels."""

from prometheus_client import CollectorRegistry, Gauge, Histogram, generate_latest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tracecause.storage.postgres import DEFAULT_WORKSPACE_ID
from tracecause.storage.schema import JobRow


class ApiMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests = Histogram(
            "tracecause_http_seconds",
            "Completed HTTP request duration",
            ["method", "route", "status"],
            registry=self.registry,
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 3, 10, 30),
        )
        self.queue = Gauge(
            "tracecause_jobs", "Durable API jobs by state", ["state"], registry=self.registry
        )

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        self.requests.labels(
            method if method in {"GET", "POST", "HEAD", "OPTIONS"} else "OTHER", route, str(status)
        ).observe(seconds)

    def render(self, factory: sessionmaker[Session]) -> bytes:
        states = ("QUEUED", "RUNNING", "RETRY_WAIT", "SUCCEEDED", "FAILED", "CANCELLED")
        with factory() as session:
            counts = dict(
                session.execute(
                    select(JobRow.status, func.count())
                    .where(
                        JobRow.kind.in_(("API_IMPORT", "INVESTIGATE")),
                        JobRow.workspace_id == DEFAULT_WORKSPACE_ID,
                    )
                    .group_by(JobRow.status)
                ).all()
            )
        for state in states:
            self.queue.labels(state).set(counts.get(state, 0))
        return generate_latest(self.registry)
