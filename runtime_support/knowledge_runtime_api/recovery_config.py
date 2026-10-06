from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable is missing: {name}")
    return value


@dataclass(frozen=True, slots=True)
class RecoverySettings:
    database_url: str
    query_database_url: str | None
    qdrant_url: str
    qdrant_collection: str
    qdrant_vector_size: int
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    neo4j_database: str
    llm_base_url: str
    llm_api_key: str | None
    protocol_model: str
    protocol_max_tokens: int
    protocol_timeout_seconds: float
    embedding_model: str
    protocol_input_cost_per_million: float
    protocol_output_cost_per_million: float
    expert_canary_model: str
    expert_canary_timeout_seconds: float
    worker_url: str
    log_level: str
    cors_origins: tuple[str, ...]
    job_max_attempts: int
    worker_poll_seconds: float
    worker_batch_size: int
    llm_socket_path: str | None = None
    authorization_enabled: bool = False
    qdrant_indexing_threshold_kb: int = 1000
    qdrant_full_scan_threshold_kb: int = 1000


@lru_cache(maxsize=1)
def get_recovery_settings() -> RecoverySettings:
    origins = os.getenv(
        "CORS_ORIGINS",
        "http://127.0.0.1:13000,http://localhost:13000",
    )
    api_key = os.getenv("LLM_API_KEY", "").strip() or None
    return RecoverySettings(
        database_url=_required("DATABASE_URL"),
        query_database_url=os.getenv("QUERY_DATABASE_URL", "").strip() or None,
        qdrant_url=os.getenv("QDRANT_URL", "http://qdrant:6333").rstrip("/"),
        qdrant_collection=os.getenv("QDRANT_COLLECTION", "knowledge_v1"),
        qdrant_vector_size=int(os.getenv("QDRANT_VECTOR_SIZE", "768")),
        neo4j_uri=os.getenv("NEO4J_URI", "bolt://neo4j:7687"),
        neo4j_user=os.getenv("NEO4J_USER", "neo4j"),
        neo4j_password=_required("NEO4J_PASSWORD"),
        neo4j_database=os.getenv("NEO4J_DATABASE", "neo4j"),
        llm_base_url=os.getenv(
            "LLM_BASE_URL", "http://host.docker.internal:1234/v1"
        ).rstrip("/"),
        llm_api_key=api_key,
        protocol_model=_required("PROTOCOL_MODEL"),
        protocol_max_tokens=max(512, int(os.getenv("PROTOCOL_MAX_TOKENS", "12000"))),
        protocol_timeout_seconds=max(
            30.0, float(os.getenv("PROTOCOL_TIMEOUT_SECONDS", "1800"))
        ),
        embedding_model=_required("EMBEDDING_MODEL"),
        protocol_input_cost_per_million=float(
            os.getenv("PROTOCOL_INPUT_COST_PER_MILLION", "0")
        ),
        protocol_output_cost_per_million=float(
            os.getenv("PROTOCOL_OUTPUT_COST_PER_MILLION", "0")
        ),
        expert_canary_model=os.getenv("EXPERT_CANARY_MODEL", "").strip()
        or _required("PROTOCOL_MODEL"),
        expert_canary_timeout_seconds=max(
            30.0, float(os.getenv("EXPERT_CANARY_TIMEOUT_SECONDS", "300"))
        ),
        worker_url=os.getenv("WORKER_URL", "http://worker:8081").rstrip("/"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        cors_origins=tuple(item.strip() for item in origins.split(",") if item.strip()),
        job_max_attempts=max(1, int(os.getenv("JOB_MAX_ATTEMPTS", "5"))),
        worker_poll_seconds=max(0.25, float(os.getenv("WORKER_POLL_SECONDS", "2"))),
        worker_batch_size=max(1, int(os.getenv("WORKER_BATCH_SIZE", "10"))),
        llm_socket_path=os.getenv("LLM_SOCKET_PATH", "").strip() or None,
        authorization_enabled=os.getenv("AUTHORIZATION_ENABLED", "false").strip().lower()
        in {"1", "true", "yes", "on"},
        qdrant_indexing_threshold_kb=max(
            100,
            int(os.getenv("QDRANT_INDEXING_THRESHOLD_KB", "1000")),
        ),
        qdrant_full_scan_threshold_kb=max(
            100,
            int(os.getenv("QDRANT_FULL_SCAN_THRESHOLD_KB", "1000")),
        ),
    )
