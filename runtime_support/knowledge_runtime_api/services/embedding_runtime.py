from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any

import httpx

from ..recovery_config import RecoverySettings


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    vectors: list[list[float]]
    model: str
    dimension: int
    latency_ms: int
    usage: dict[str, Any]


class OpenAICompatibleEmbeddingAdapter:
    def __init__(self, settings: RecoverySettings) -> None:
        self.settings = settings

    def embed(self, texts: list[str]) -> EmbeddingBatch:
        if not texts:
            raise ValueError("At least one embedding input is required")
        headers = {"Content-Type": "application/json"}
        if self.settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"
        started = perf_counter()
        socket_path = self.settings.llm_socket_path
        transport = httpx.HTTPTransport(uds=socket_path) if socket_path else None
        base_url = "http://model/v1" if socket_path else self.settings.llm_base_url
        with httpx.Client(
            timeout=httpx.Timeout(120.0, connect=10.0), transport=transport
        ) as client:
            response = client.post(
                f"{base_url}/embeddings",
                headers=headers,
                json={
                    "model": self.settings.embedding_model,
                    "input": texts,
                },
            )
            response.raise_for_status()
        payload = response.json()
        data = sorted(payload.get("data") or [], key=lambda item: item.get("index", 0))
        vectors = [list(map(float, item["embedding"])) for item in data]
        if len(vectors) != len(texts):
            raise RuntimeError("Embedding provider returned an unexpected vector count")
        dimension = len(vectors[0])
        if dimension != self.settings.qdrant_vector_size:
            raise RuntimeError(
                f"Embedding dimension {dimension} does not match configured "
                f"QDRANT_VECTOR_SIZE={self.settings.qdrant_vector_size}"
            )
        return EmbeddingBatch(
            vectors=vectors,
            model=str(payload.get("model") or self.settings.embedding_model),
            dimension=dimension,
            latency_ms=round((perf_counter() - started) * 1000),
            usage=dict(payload.get("usage") or {}),
        )
