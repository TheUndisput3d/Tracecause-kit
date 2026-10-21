"""Validated default YAML and explicit runtime environment overrides."""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from tracecause.contracts.models import GenerationMode, RetrievalMode


class StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApplicationDefaults(StrictConfig):
    workspace_id: UUID
    public_read_only: bool
    generation_provider: GenerationMode


class TelemetryDefaults(StrictConfig):
    bucket_seconds: int = Field(ge=1, le=60)
    minimum_baseline_buckets: int = Field(ge=1, le=60)
    minimum_latency_samples: int = Field(ge=1, le=10000)
    baseline_seconds: int = Field(ge=10, le=3600)
    topology_max_hops: int = Field(ge=1, le=3)
    latency_mad_floor_ms: float = Field(gt=0)
    error_mad_floor: float = Field(gt=0, le=1)
    count_mad_floor: float = Field(gt=0)
    anomaly_z_threshold: float = Field(gt=0)
    anomaly_z_clip: float = Field(gt=0)


class RetrievalDefaults(StrictConfig):
    embedding_model: str
    embedding_revision: str | None
    dimensions: int = Field(ge=1, le=4096)
    chunk_tokens: int = Field(ge=1, le=220)
    chunk_overlap_tokens: int = Field(ge=0, le=219)
    chunk_max_tokens: int = Field(ge=1, le=256)
    exact_corpus_limit: int = Field(ge=1, le=20000)
    per_modality_k: int = Field(ge=1, le=40)
    rrf_constant: int = Field(ge=1, le=1000)
    max_candidates: int = Field(ge=1, le=20)
    max_nodes: int = Field(ge=1, le=80)
    max_edges: int = Field(ge=1, le=120)
    max_intermediate_evidence: int = Field(ge=1, le=60)
    max_final_evidence: int = Field(ge=1, le=12)
    max_paths: int = Field(ge=1, le=3)
    mode: RetrievalMode

    @model_validator(mode="after")
    def valid_chunking(self) -> "RetrievalDefaults":
        if self.chunk_overlap_tokens >= self.chunk_tokens:
            raise ValueError("chunk overlap must be shorter than chunk")
        if self.chunk_tokens > self.chunk_max_tokens:
            raise ValueError("target chunk length exceeds maximum")
        if (
            self.embedding_model == "sentence-transformers/all-MiniLM-L6-v2"
            and self.dimensions != 384
        ):
            raise ValueError("MiniLM embedding dimension must be 384")
        return self


class GenerationDefaults(StrictConfig):
    ollama_url: str
    model: str
    context_tokens: int = Field(ge=1024, le=8192)
    max_input_tokens: int = Field(ge=1, le=6000)
    max_output_tokens: int = Field(ge=1, le=1000)
    temperature: float = Field(ge=0, le=2)
    max_concurrency: Literal[1]
    deadline_seconds: int = Field(ge=1, le=180)
    max_repairs: int = Field(ge=0, le=1)

    @model_validator(mode="after")
    def valid_budget(self) -> "GenerationDefaults":
        if self.max_input_tokens + self.max_output_tokens > self.context_tokens:
            raise ValueError("generation token budget exceeds context")
        return self


class HeuristicWeights(StrictConfig):
    latency: float
    error: float
    onset: float
    proximity: float
    anomalous_callers: float
    missingness_penalty: float


class RankingDefaults(StrictConfig):
    default: Literal["heuristic", "learned"]
    feature_schema_version: str
    heuristic_weights: HeuristicWeights


class JobDefaults(StrictConfig):
    lease_seconds: int = Field(ge=5, le=300)
    heartbeat_seconds: int = Field(ge=1, le=60)
    poll_seconds: float = Field(gt=0, le=10)
    max_attempts: int = Field(ge=1, le=10)
    max_queued_per_operator: int = Field(ge=1, le=10)

    @model_validator(mode="after")
    def heartbeat_before_lease(self) -> "JobDefaults":
        if self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError("heartbeat must be shorter than lease")
        return self


class LimitsDefaults(StrictConfig):
    question_characters: int = Field(ge=1, le=2000)
    upload_file_mib: int = Field(ge=1, le=25)
    upload_bundle_mib: int = Field(ge=1, le=100)
    upload_file_count: int = Field(ge=1, le=100)
    graph_query_timeout_seconds: int = Field(ge=1, le=3)


class DefaultConfiguration(StrictConfig):
    schema_version: Literal["1.0"]
    application: ApplicationDefaults
    telemetry: TelemetryDefaults
    retrieval: RetrievalDefaults
    generation: GenerationDefaults
    ranking: RankingDefaults
    jobs: JobDefaults
    limits: LimitsDefaults


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRACECAUSE_", extra="ignore")

    public_read_only: bool = True
    generation_provider: GenerationMode = GenerationMode.TEMPLATE
    enable_local_model: bool = False
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_revision: str = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
    embedding_dimensions: int = Field(default=384, ge=1, le=4096)
    ollama_model: str = "qwen3:4b-instruct-2507-q4_K_M"
    ollama_model_digest: str | None = None
    ollama_url: str = "http://ollama:11434"
    artifact_root: Path = Path("/var/lib/tracecause/artifacts")
    staging_root: Path = Path("/var/lib/tracecause/staging")
    pg_host: str = "postgres"
    pg_port: int = Field(default=5432, ge=1, le=65535)
    pg_database: str = "tracecause"
    pg_user: str = "tracecause_app"
    pg_password: SecretStr | None = None
    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_auth: SecretStr | None = None
    operator_token: SecretStr | None = None
    question_characters: int = Field(default=2000, ge=1, le=2000)
    graph_max_hops: int = Field(default=3, ge=1, le=3)
    graph_max_nodes: int = Field(default=80, ge=1, le=80)
    graph_max_edges: int = Field(default=120, ge=1, le=120)
    generation_deadline_seconds: int = Field(default=180, ge=1, le=180)
    generation_max_concurrency: Literal[1] = 1
    generation_context_tokens: int = Field(default=8192, ge=1024, le=8192)
    generation_input_tokens: int = Field(default=6000, ge=1, le=6000)
    generation_output_tokens: int = Field(default=1000, ge=1, le=1000)

    @field_validator("pg_host", "pg_database", "pg_user")
    @classmethod
    def nonempty_db_value(cls, value: str) -> str:
        if not value or len(value) > 200 or any(char.isspace() for char in value):
            raise ValueError("database identity must be nonempty and contain no whitespace")
        return value

    @field_validator("neo4j_uri")
    @classmethod
    def valid_neo4j_uri(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"bolt", "neo4j"} or not parsed.hostname:
            raise ValueError("Neo4j URI must use bolt:// or neo4j:// with a host")
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("Neo4j URI must not embed credentials or paths")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Neo4j URI has an invalid port") from exc
        if port is None or port < 1:
            raise ValueError("Neo4j URI needs an explicit port")
        return value

    @field_validator("embedding_revision")
    @classmethod
    def pinned_revision(cls, value: str) -> str:
        if len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("embedding revision must be a 40-character commit hash")
        return value

    @field_validator("artifact_root", "staging_root")
    @classmethod
    def safe_root(cls, value: Path) -> Path:
        if not value.is_absolute() or value == Path("/") or "gold" in value.parts:
            raise ValueError("artifact/staging root must be an absolute non-gold directory")
        if any(parent.is_symlink() for parent in (value, *value.parents)):
            raise ValueError("artifact/staging root cannot cross a symlink")
        return value

    @model_validator(mode="after")
    def valid_provider(self) -> "RuntimeSettings":
        if self.embedding_model == "sentence-transformers/all-MiniLM-L6-v2":
            if self.embedding_dimensions != 384:
                raise ValueError("MiniLM requires 384 embedding dimensions")
        if self.generation_provider is GenerationMode.OLLAMA:
            if self.public_read_only or not self.enable_local_model:
                raise ValueError("local model requires explicit operator opt-in")
            if not self.ollama_model_digest:
                raise ValueError("local model requires a pinned digest")
            if self.ollama_model != "qwen3:4b-instruct-2507-q4_K_M":
                raise ValueError("local model must be the reviewed Qwen tag")
            if (
                not self.ollama_model_digest.startswith("sha256:")
                or len(self.ollama_model_digest) != 71
                or any(char not in "0123456789abcdef" for char in self.ollama_model_digest[7:])
            ):
                raise ValueError("local model digest must be sha256 plus 64 hex characters")
            if self.ollama_url not in {"http://ollama:11434", "http://127.0.0.1:11434"}:
                raise ValueError("local model endpoint is not allowlisted")
        if not self.public_read_only and self.operator_token is None:
            raise ValueError("operator mode requires an operator token")
        if (
            self.generation_input_tokens + self.generation_output_tokens
            > self.generation_context_tokens
        ):
            raise ValueError("generation token budget exceeds context")
        return self


def load_defaults(path: Path) -> DefaultConfiguration:
    with path.open(encoding="utf-8") as source:
        data = yaml.safe_load(source)
    if not isinstance(data, dict):
        raise ValueError("default configuration must be a mapping")
    return DefaultConfiguration.model_validate(data)


def _read_secret_file(name: str, env: Mapping[str, str]) -> SecretStr | None:
    direct = env.get(f"TRACECAUSE_{name}")
    file_name = env.get(f"TRACECAUSE_{name}_FILE")
    if direct and file_name:
        raise ValueError(f"provide either TRACECAUSE_{name} or its _FILE variant")
    if file_name:
        content = Path(file_name).read_text(encoding="utf-8").strip()
        if not content:
            raise ValueError(f"TRACECAUSE_{name}_FILE is empty")
        return SecretStr(content)
    return SecretStr(direct) if direct else None


def load_settings(path: Path, env: dict[str, str] | None = None) -> RuntimeSettings:
    defaults = load_defaults(path)
    values: dict[str, Any] = {
        "public_read_only": defaults.application.public_read_only,
        "generation_provider": defaults.application.generation_provider,
        "embedding_model": defaults.retrieval.embedding_model,
        "embedding_dimensions": defaults.retrieval.dimensions,
        "ollama_model": defaults.generation.model,
        "ollama_url": defaults.generation.ollama_url,
        "question_characters": defaults.limits.question_characters,
        "graph_max_hops": defaults.telemetry.topology_max_hops,
        "graph_max_nodes": defaults.retrieval.max_nodes,
        "graph_max_edges": defaults.retrieval.max_edges,
        "generation_deadline_seconds": defaults.generation.deadline_seconds,
        "generation_max_concurrency": defaults.generation.max_concurrency,
        "generation_context_tokens": defaults.generation.context_tokens,
        "generation_input_tokens": defaults.generation.max_input_tokens,
        "generation_output_tokens": defaults.generation.max_output_tokens,
    }
    if defaults.retrieval.embedding_revision:
        values["embedding_revision"] = defaults.retrieval.embedding_revision
    effective_env = os.environ if env is None else env
    for key in RuntimeSettings.model_fields:
        source_key = f"TRACECAUSE_{key.upper()}"
        if key not in {"pg_password", "neo4j_auth", "operator_token"}:
            if source_key in effective_env:
                values[key] = effective_env[source_key]
    for name in ("PG_PASSWORD", "NEO4J_AUTH", "OPERATOR_TOKEN"):
        secret = _read_secret_file(name, effective_env)
        if secret is not None:
            values[name.lower()] = secret
    return RuntimeSettings.model_validate(values)
