"""Project daughter canonical knowledge into isolated Qdrant and Neo4j spaces."""

from __future__ import annotations

import hashlib
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from neo4j import GraphDatabase

from autonomy_store import PROJECT_ID, connect
from knowledge_runtime_api.recovery_config import get_recovery_settings
from knowledge_runtime_api.services.embedding_runtime import OpenAICompatibleEmbeddingAdapter
from knowledge_runtime_api.services.qdrant_runtime import QdrantRuntime


logger = logging.getLogger("galactica.autonomy.projection")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
settings = get_recovery_settings()
ACCOUNT_ID = os.environ["CLIENT_ACCOUNT_ID"]
RELATIONS = frozenset({'SUPPORTS','CONTRADICTS','SUPERSEDES','REQUIRES','ASSOCIATED_WITH'})
def assert_embedding_space():
    if not settings.qdrant_collection.startswith('sfera_'):
        raise RuntimeError('Use an instance-specific sfera_ collection')


def clean_text(value: str) -> str:
    return " ".join(value.split())


def claim() -> dict | None:
    with connect() as database:
        with database.transaction():
            row = database.execute(
                """
                WITH candidate AS (
                  SELECT o.id FROM galactica_autonomy.projection_outbox o
                  WHERE o.status IN ('pending','failed')
                    AND o.next_retry_at <= now() AND o.attempts < o.max_attempts
                    AND (
                      o.target='qdrant'
                      OR (o.target='neo4j' AND o.entity_type='knowledge'
                        AND EXISTS (
                          SELECT 1 FROM galactica_autonomy.projection_outbox q
                          WHERE q.entity_type='knowledge' AND q.entity_id=o.entity_id
                            AND q.entity_version=o.entity_version AND q.target='qdrant'
                            AND q.status='succeeded'))
                      OR (o.target='neo4j' AND o.entity_type='relation'
                        AND EXISTS (
                          SELECT 1 FROM galactica_autonomy.relations r
                          JOIN galactica_autonomy.projection_outbox qs
                            ON qs.entity_type='knowledge' AND qs.entity_id=r.source_id
                           AND qs.target='qdrant' AND qs.status='succeeded'
                          JOIN galactica_autonomy.projection_outbox qt
                            ON qt.entity_type='knowledge' AND qt.entity_id=r.target_id
                           AND qt.target='qdrant' AND qt.status='succeeded'
                          WHERE r.id=o.entity_id))
                    )
                  ORDER BY CASE o.target WHEN 'qdrant' THEN 0 ELSE 1 END, o.id
                  FOR UPDATE SKIP LOCKED LIMIT 1
                )
                UPDATE galactica_autonomy.projection_outbox o
                   SET status='processing',attempts=o.attempts+1,locked_at=now()
                  FROM candidate WHERE o.id=candidate.id RETURNING o.*
                """
            ).fetchone()
            return row


def finish(job: dict, error: Exception | None = None) -> None:
    with connect() as database:
        with database.transaction():
            if error is None:
                database.execute(
                    """UPDATE galactica_autonomy.projection_outbox SET
                       status='succeeded',processed_at=now(),locked_at=NULL,last_error=NULL
                       WHERE id=%s""",
                    (job["id"],),
                )
            else:
                dead = job["attempts"] >= job["max_attempts"]
                delay = min(300, 2 ** min(8, job["attempts"]))
                database.execute(
                    """UPDATE galactica_autonomy.projection_outbox SET
                       status=%s,next_retry_at=now()+(%s * interval '1 second'),
                       locked_at=NULL,last_error=%s WHERE id=%s""",
                    ("dead" if dead else "failed", delay,
                     f"{type(error).__name__}: {str(error)[:500]}", job["id"]),
                )


def project_qdrant(job: dict) -> None:
    with connect() as database:
        row = database.execute(
            "SELECT * FROM galactica_autonomy.knowledge WHERE id=%s", (job["entity_id"],)
        ).fetchone()
    qdrant = QdrantRuntime(settings)
    if job["operation"] == "delete" or row is None or row["status"] != "active":
        qdrant.delete(job["entity_id"])
        return
    text = clean_text(row["title"] + "\n" + row["body"])
    batch = OpenAICompatibleEmbeddingAdapter(settings).embed([text])
    payload = {
        "postgres_id": str(row["id"]),
        "project_id": str(PROJECT_ID),
        "account_id": ACCOUNT_ID,
        "visibility_scope": "account",
        "privacy_schema_version": "1.0",
        "source_table": "galactica_autonomy.knowledge",
        "layer": "knowledge",
        "kind": row["kind"],
        "area": row["area_key"],
        "status": row["status"],
        "memory_layer": row["memory_layer"],
        "epistemic_status": row["epistemic_status"],
        "semantic_tags": row["tags"],
        "source_message_ids": row["source_message_ids"],
        "source_revision": row["updated_at"].astimezone(UTC).isoformat(),
        "embedding_model": batch.model,
        "embedding_weights_sha256": EXPECTED_WEIGHTS_SHA,
        "embedding_dimension": batch.dimension,
        "text_prep_version": PREP_VERSION,
        "content_sha256": row["content_sha256"],
        "version": row["version"],
    }
    qdrant.upsert(point_id=row["id"], vector=batch.vectors[0], payload=payload)


def project_neo4j(job: dict) -> None:
    with connect() as database:
        if job["entity_type"] == "knowledge":
            row = database.execute(
                "SELECT * FROM galactica_autonomy.knowledge WHERE id=%s", (job["entity_id"],)
            ).fetchone()
        else:
            row = database.execute(
                "SELECT * FROM galactica_autonomy.relations WHERE id=%s", (job["entity_id"],)
            ).fetchone()
    with GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
        connection_timeout=5.0,
    ) as driver:
        if job["entity_type"] == "knowledge":
            if job["operation"] == "delete" or row is None or row["status"] != "active":
                driver.execute_query(
                    "MATCH (n:AutonomyKnowledge {canonical_id:$id}) DETACH DELETE n",
                    id=str(job["entity_id"]), database_=settings.neo4j_database,
                )
            else:
                driver.execute_query(
                    """MERGE (n:AutonomyKnowledge {canonical_id:$id})
                       SET n.project_id=$project_id,n.account_id=$account_id,
                           n.title=$title,n.kind=$kind,n.area=$area,
                           n.memory_layer=$memory_layer,
                           n.epistemic_status=$epistemic_status,n.tags=$tags,
                           n.version=$version,n.content_sha256=$sha,n.updated_at=datetime()""",
                    id=str(row["id"]), project_id=str(PROJECT_ID), account_id=ACCOUNT_ID,
                    title=row["title"], kind=row["kind"], area=row["area_key"],
                    memory_layer=row["memory_layer"],
                    epistemic_status=row["epistemic_status"], tags=row["tags"],
                    version=row["version"], sha=row["content_sha256"],
                    database_=settings.neo4j_database,
                )
        else:
            if job["operation"] == "delete" or row is None or row["status"] != "active":
                driver.execute_query(
                    "MATCH ()-[r {canonical_id:$id}]->() DELETE r",
                    id=str(job["entity_id"]), database_=settings.neo4j_database,
                )
            else:
                relation = row["relation_type"].upper()
                if relation not in RELATIONS:
                    raise ValueError("Relation type is not allowed")
                records, summary, _ = driver.execute_query(
                    f"""MATCH (s:AutonomyKnowledge {{canonical_id:$source}})
                        MATCH (t:AutonomyKnowledge {{canonical_id:$target}})
                        MERGE (s)-[r:{relation} {{canonical_id:$id}}]->(t)
                        SET r.project_id=$project_id,r.account_id=$account_id,
                            r.evidence_message_ids=$evidence,
                            r.verification_status=$verification_status
                        RETURN r.canonical_id AS id""",
                    source=str(row["source_id"]), target=str(row["target_id"]),
                    id=str(row["id"]), project_id=str(PROJECT_ID), account_id=ACCOUNT_ID,
                    evidence=row["evidence_message_ids"],
                    verification_status=row["verification_status"],
                    database_=settings.neo4j_database,
                )
                if not records or records[0]["id"] != str(row["id"]):
                    raise RuntimeError("Relation endpoints are not yet present in Neo4j")


def recover_stale() -> int:
    with connect() as database:
        with database.transaction():
            result = database.execute(
                """UPDATE galactica_autonomy.projection_outbox
                   SET status='failed',locked_at=NULL,next_retry_at=now(),
                       last_error='Recovered stale projection lock'
                   WHERE status='processing' AND locked_at < now()-interval '10 minutes'"""
            )
            return result.rowcount


def rebuild() -> dict:
    assert_embedding_space()
    qdrant = QdrantRuntime(settings)
    with qdrant._client() as client:
        response = client.delete(f"/collections/{settings.qdrant_collection}")
        if response.status_code not in {200, 404}:
            response.raise_for_status()
    with connect() as database:
        with database.transaction():
            database.execute(
                """UPDATE galactica_autonomy.projection_outbox SET
                   status='pending',attempts=0,next_retry_at=now(),locked_at=NULL,
                   last_error=NULL,processed_at=NULL"""
            )
            count = database.execute(
                "SELECT count(*) AS count FROM galactica_autonomy.projection_outbox"
            ).fetchone()["count"]
    return {"requeued": count, "collection": settings.qdrant_collection}


def main() -> None:
    assert_embedding_space()
    if len(os.sys.argv) > 1 and os.sys.argv[1] == "rebuild":
        print(rebuild())
        return
    last_recovery = 0.0
    while True:
        if time.monotonic() - last_recovery > 600:
            recover_stale()
            last_recovery = time.monotonic()
        job = claim()
        if job is None:
            time.sleep(5)
            continue
        try:
            if job["target"] == "qdrant":
                project_qdrant(job)
            else:
                project_neo4j(job)
            finish(job)
            logger.info("Projected %s %s %s", job["target"], job["entity_type"], job["entity_id"])
        except Exception as exc:
            finish(job, error=exc)
            logger.exception("Projection failed: %s", job["id"])


if __name__ == "__main__":
    main()
