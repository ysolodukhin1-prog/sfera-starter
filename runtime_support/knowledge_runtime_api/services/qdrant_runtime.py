from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

from ..recovery_config import RecoverySettings


@dataclass(frozen=True, slots=True)
class QdrantCandidate:
    knowledge_item_id: UUID
    score: float
    payload: dict[str, Any]


class QdrantRuntime:
    def __init__(self, settings: RecoverySettings) -> None:
        self.settings = settings

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.settings.qdrant_url,
            timeout=httpx.Timeout(30.0, connect=5.0),
        )

    def ready(self) -> bool:
        try:
            with self._client() as client:
                response = client.get("/readyz")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def ensure_collection(self) -> None:
        collection = self.settings.qdrant_collection
        indexing_threshold = int(
            getattr(self.settings, "qdrant_indexing_threshold_kb", 1000)
        )
        full_scan_threshold = int(
            getattr(self.settings, "qdrant_full_scan_threshold_kb", 1000)
        )
        with self._client() as client:
            response = client.get(f"/collections/{collection}")
            if response.status_code == 404:
                create = client.put(
                    f"/collections/{collection}",
                    json={
                        "vectors": {
                            "size": self.settings.qdrant_vector_size,
                            "distance": "Cosine",
                        },
                        "optimizers_config": {
                            "indexing_threshold": indexing_threshold,
                        },
                        "hnsw_config": {
                            "full_scan_threshold": full_scan_threshold,
                        },
                    },
                )
                create.raise_for_status()
            else:
                response.raise_for_status()
                configured_size = (
                    response.json()
                    .get("result", {})
                    .get("config", {})
                    .get("params", {})
                    .get("vectors", {})
                    .get("size")
                )
                if configured_size != self.settings.qdrant_vector_size:
                    raise RuntimeError(
                        f"Qdrant collection vector size is {configured_size}, expected "
                        f"{self.settings.qdrant_vector_size}"
                    )
                config = response.json().get("result", {}).get("config", {})
                current_indexing_threshold = (
                    config.get("optimizer_config", {}).get("indexing_threshold")
                )
                current_full_scan_threshold = (
                    config.get("hnsw_config", {}).get("full_scan_threshold")
                )
                if (
                    current_indexing_threshold != indexing_threshold
                    or current_full_scan_threshold != full_scan_threshold
                ):
                    update = client.patch(
                        f"/collections/{collection}",
                        json={
                            "optimizers_config": {
                                "indexing_threshold": indexing_threshold,
                            },
                            "hnsw_config": {
                                "full_scan_threshold": full_scan_threshold,
                            },
                        },
                    )
                    update.raise_for_status()
            # Payload indexes are idempotent and must also be reconciled on an
            # existing collection. Otherwise an upgraded installation keeps
            # the old unscoped index layout until a destructive rebuild.
            for field_name in (
                "project_id",
                "account_id",
                "knowledge_domain_id",
                "owner_principal_id",
                "visibility_scope",
                "privacy_schema_version",
                "layer",
                "status",
                "kind",
                "source_table",
                "semantic_tags",
                "tag_categories",
            ):
                index_response = client.put(
                    f"/collections/{collection}/index",
                    params={"wait": "true"},
                    json={"field_name": field_name, "field_schema": "keyword"},
                )
                if index_response.status_code not in {200, 201, 409}:
                    index_response.raise_for_status()

    def delete_project(self, project_id: UUID) -> None:
        self.ensure_collection()
        with self._client() as client:
            response = client.post(
                f"/collections/{self.settings.qdrant_collection}/points/delete",
                params={"wait": "true"},
                json={
                    "filter": {
                        "must": [
                            {
                                "key": "project_id",
                                "match": {"value": str(project_id)},
                            }
                        ]
                    }
                },
            )
            response.raise_for_status()

    def upsert(
        self,
        *,
        point_id: UUID,
        vector: list[float],
        payload: dict[str, Any],
    ) -> None:
        self.ensure_collection()
        with self._client() as client:
            response = client.put(
                f"/collections/{self.settings.qdrant_collection}/points",
                params={"wait": "true"},
                json={
                    "points": [
                        {
                            "id": str(point_id),
                            "vector": vector,
                            "payload": payload,
                        }
                    ]
                },
            )
            response.raise_for_status()

    def get_vectors(
        self,
        point_ids: list[UUID],
        *,
        with_payload: bool = False,
        max_points: int = 250,
    ) -> dict[UUID, list[float]]:
        if max_points <= 0:
            raise ValueError("max_points must be positive")
        unique_ids = list(dict.fromkeys(point_ids))
        if len(unique_ids) > max_points:
            raise ValueError(
                f"Requested {len(unique_ids)} vectors, maximum is {max_points}"
            )
        if not unique_ids:
            return {}
        with self._client() as client:
            response = client.post(
                f"/collections/{self.settings.qdrant_collection}/points",
                json={
                    "ids": [str(value) for value in unique_ids],
                    "with_payload": with_payload,
                    "with_vector": True,
                },
            )
            response.raise_for_status()
        points = response.json().get("result") or []
        result: dict[UUID, list[float]] = {}
        for point in points:
            vector = point.get("vector")
            if isinstance(vector, list):
                result[UUID(str(point["id"]))] = [float(value) for value in vector]
        return result

    def delete(self, point_id: UUID) -> None:
        with self._client() as client:
            response = client.post(
                f"/collections/{self.settings.qdrant_collection}/points/delete",
                params={"wait": "true"},
                json={"points": [str(point_id)]},
            )
            if response.status_code != 404:
                response.raise_for_status()

    def query(
        self,
        *,
        vector: list[float],
        project_id: UUID,
        account_id: UUID,
        top_k: int,
        kinds: list[str] | None = None,
        scope_ids: list[UUID] | None = None,
    ) -> list[QdrantCandidate]:
        must: list[dict[str, Any]] = [
            {"key": "project_id", "match": {"value": str(project_id)}},
            {"key": "account_id", "match": {"value": str(account_id)}},
            {"key": "privacy_schema_version", "match": {"value": "1.0"}},
            {"key": "status", "match": {"value": "active"}},
            # The collection contains every semantic layer. Direct RAG must
            # discover only rows that have a canonical KnowledgeItem source.
            {"key": "layer", "match": {"value": "knowledge"}},
            {
                "key": "source_table",
                "match": {"value": "knowledge_items"},
            },
        ]
        if kinds:
            must.append({"key": "kind", "match": {"any": kinds}})
        if scope_ids is not None:
            if not scope_ids:
                return []
            must.append({"has_id": [str(value) for value in scope_ids]})
        with self._client() as client:
            response = client.post(
                f"/collections/{self.settings.qdrant_collection}/points/query",
                json={
                    "query": vector,
                    "filter": {"must": must},
                    "limit": top_k,
                    "with_payload": True,
                    "with_vector": False,
                },
            )
            response.raise_for_status()
        result = response.json().get("result") or {}
        points = result.get("points") if isinstance(result, dict) else result
        candidates: list[QdrantCandidate] = []
        for point in points or []:
            payload = dict(point.get("payload") or {})
            canonical_id = payload.get("postgres_id") or point.get("id")
            candidates.append(
                QdrantCandidate(
                    knowledge_item_id=UUID(str(canonical_id)),
                    score=float(point.get("score") or 0.0),
                    payload=payload,
                )
            )
        return candidates

    def delete_collection(self) -> None:
        with self._client() as client:
            response = client.delete(
                f"/collections/{self.settings.qdrant_collection}"
            )
            if response.status_code != 404:
                response.raise_for_status()

    def point_count(self) -> int:
        with self._client() as client:
            response = client.get(
                f"/collections/{self.settings.qdrant_collection}"
            )
            if response.status_code == 404:
                return 0
            response.raise_for_status()
        return int(response.json().get("result", {}).get("points_count") or 0)
