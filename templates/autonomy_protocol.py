"""Protocolize each finished CLIENT task with the VPS-local chat model.

PostgreSQL is canonical. The model proposes sourced knowledge; this worker
validates it and commits knowledge, relations, and projection jobs together.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from telemetry import record as telemetry_record
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from psycopg.types.json import Jsonb

from autonomy_store import PROJECT_ID, SECRET_PATTERNS, connect, digest


logger = logging.getLogger("galactica.autonomy.protocol")
KINDS = frozenset({
    "concept", "procedure", "decision", "requirement", "constraint",
    "lesson", "failure", "alternative", "observation",
})
RELATIONS = frozenset({
    "SUPPORTS", "CONTRADICTS", "SUPERSEDES", "REQUIRES", "ASSOCIATED_WITH",
})
SOURCE_FIELDS = ("result", "actions", "decisions", "limitations", "checks")
MAX_ITEMS = 5
MAX_RELATIONS = 10
SCHEMA = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "string", "enum": ["1.0"]},
        "items": {
            "type": "array", "maxItems": MAX_ITEMS,
            "items": {
                "type": "object",
                "properties": {
                    "local_id": {"type": "string"},
                    "kind": {"type": "string", "enum": sorted(KINDS)},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "source_ref": {"type": "string"},
                    "evidence_quote": {"type": "string"},
                },
                "required": ["local_id", "kind", "title", "body",
                             "source_ref", "evidence_quote"],
                "additionalProperties": False,
            },
        },
        "relations": {
            "type": "array", "maxItems": MAX_RELATIONS,
            "items": {
                "type": "object",
                "properties": {
                    "source_ref": {"type": "string"},
                    "target_ref": {"type": "string"},
                    "relation_type": {"type": "string", "enum": sorted(RELATIONS)},
                    "evidence_ref": {"type": "string"},
                    "evidence_quote": {"type": "string"},
                },
                "required": ["source_ref", "target_ref", "relation_type",
                             "evidence_ref", "evidence_quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["schema_version", "items", "relations"],
    "additionalProperties": False,
}


def source_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(source_text(item) for item in value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def sources(task: dict, messages: list[dict]) -> dict[str, str]:
    summary = task["summary"] or {}
    result = {"task.request": task["request_text"]}
    for field in SOURCE_FIELDS:
        value = summary.get(field)
        if value:
            result[f"summary.{field}"] = source_text(value)
    for message in messages:
        if message["completeness"] == "exact" and message["role"] in {"user", "assistant"}:
            result[f"message:{message['id']}"] = message["body"][:4000]
    return result


def safe_sources(task: dict, messages: list[dict]) -> dict[str, str]:
    values = sources(task, messages)
    if not str((task["summary"] or {}).get("result") or "").strip():
        raise ValueError("Task outcome has no result")
    if any(pattern.search(value) for value in values.values()
           for pattern in SECRET_PATTERNS):
        raise ValueError("Task outcome appears to contain a secret")
    return {key: value[:6000] for key, value in values.items()}


def claim() -> dict | None:
    with connect() as db:
        with db.transaction():
            return db.execute(
                """WITH candidate AS (
                     SELECT task_id FROM galactica_autonomy.outcome_protocol_jobs
                     WHERE status IN ('pending','failed') AND next_retry_at<=now()
                       AND attempts<max_attempts
                     ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
                   )
                   UPDATE galactica_autonomy.outcome_protocol_jobs j
                      SET status='processing', attempts=j.attempts+1, locked_at=now()
                     FROM candidate c WHERE j.task_id=c.task_id
                   RETURNING j.*"""
            ).fetchone()


def load_task(task_id: UUID) -> tuple[dict, list[dict]]:
    with connect() as db:
        task = db.execute(
            "SELECT * FROM galactica_autonomy.tasks WHERE id=%s", (task_id,)
        ).fetchone()
        messages = db.execute(
            """SELECT id,role,body,completeness FROM galactica_autonomy.messages
               WHERE task_id=%s AND completeness='exact'
               ORDER BY observed_at,id LIMIT 16""",
            (task_id,),
        ).fetchall()
    if not task or task["project_id"] != PROJECT_ID or not task["summary_sha256"]:
        raise ValueError("Unknown or unfinished CLIENT task")
    return task, messages


def generate(task: dict, source_map: dict[str, str]) -> tuple[dict, str]:
    model = os.environ["PROTOCOL_MODEL"]
    socket = os.environ["LLM_SOCKET_PATH"]
    prompt = (
        "Extract up to five durable CLIENT facts from this completed task. "
        "The task outcome is an agent report, not independent proof. "
        "Do not repeat the overall task result, which is stored separately. "
        "Use only supplied source texts. For every item copy an exact substring "
        "of its source into evidence_quote and give its source_ref. "
        "Use short stable local_id values. Relations may connect only emitted "
        "local_id values; each relation needs an exact evidence quote. "
        "Leave arrays empty when no durable facts or justified relations exist. "
        "Use observation for measured counts, checks, and completed results. "
        "Use failure only when the quoted evidence explicitly describes an error, "
        "failed action, or unmet check; a successful check is not a failure. "
        "Do not invent user preferences, external facts, or verification. "
        "Return only JSON matching the schema."
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({
                "task_id": str(task["id"]), "sources": source_map,
            }, ensure_ascii=False)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "sfera_outcome_protocol",
                            "strict": True, "schema": SCHEMA},
        },
        "temperature": 0,
        "max_tokens": 1400,
    }
    with httpx.Client(
        transport=httpx.HTTPTransport(uds=socket),
        timeout=httpx.Timeout(150.0, connect=10.0),
    ) as client:
        response = client.post("http://model/v1/chat/completions", json=payload)
        response.raise_for_status()
    data = response.json()
    telemetry_record(model_call=True,model_usage=data.get("usage"))
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
    if not isinstance(content, str):
        raise ValueError("Protocol model returned no JSON content")
    return json.loads(content), str(data.get("model") or model)


def checked_quote(source_map: dict[str, str], ref: object, quote: object) -> tuple[str, str]:
    if not isinstance(ref, str) or ref not in source_map:
        raise ValueError("Protocol cites an unknown source")
    if not isinstance(quote, str) or len(quote) < 6 or len(quote) > 600:
        raise ValueError("Protocol evidence quote is invalid")
    if quote not in source_map[ref]:
        raise ValueError("Protocol evidence is not an exact source substring")
    return ref, quote


def validate(protocol: dict, source_map: dict[str, str]) -> dict:
    if not isinstance(protocol, dict) or set(protocol) != set(SCHEMA["required"]):
        raise ValueError("Protocol object has unexpected fields")
    if protocol["schema_version"] != "1.0":
        raise ValueError("Protocol schema version mismatch")
    items, relations = protocol["items"], protocol["relations"]
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise ValueError("Invalid protocol item count")
    if not isinstance(relations, list) or len(relations) > MAX_RELATIONS:
        raise ValueError("Invalid protocol relation count")
    ids: set[str] = set()
    valid_items: list[dict] = []
    for item in items:
        try:
            if not isinstance(item, dict) or set(item) != set(SCHEMA["properties"]["items"]["items"]["required"]):
                raise ValueError("Invalid protocol item shape")
            local_id = item["local_id"]
            if not isinstance(local_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", local_id):
                raise ValueError("Invalid protocol local ID")
            if local_id == "outcome" or local_id in ids:
                raise ValueError("Duplicate or reserved protocol local ID")
            if item["kind"] not in KINDS:
                raise ValueError("Invalid knowledge kind")
            if not isinstance(item["title"], str) or not 1 <= len(item["title"].strip()) <= 180:
                raise ValueError("Invalid knowledge title")
            if not isinstance(item["body"], str) or not 1 <= len(item["body"].strip()) <= 1200:
                raise ValueError("Invalid knowledge body")
            checked_quote(source_map, item["source_ref"], item["evidence_quote"])
            if any(pattern.search(item["title"] + "\n" + item["body"])
                   for pattern in SECRET_PATTERNS):
                raise ValueError("Protocol item appears to contain a secret")
        except ValueError as error:
            logger.warning("Discarding unsupported protocol item: %s", error)
            continue
        ids.add(local_id)
        valid_items.append(item)
    valid_relations: list[dict] = []
    for relation in relations:
        try:
            if not isinstance(relation, dict) or set(relation) != set(SCHEMA["properties"]["relations"]["items"]["required"]):
                raise ValueError("Invalid protocol relation shape")
            if relation["source_ref"] not in ids or relation["target_ref"] not in ids:
                raise ValueError("Protocol relation endpoints must be emitted items")
            if relation["source_ref"] == relation["target_ref"] or relation["relation_type"] not in RELATIONS:
                raise ValueError("Invalid protocol relation")
            checked_quote(source_map, relation["evidence_ref"], relation["evidence_quote"])
        except ValueError as error:
            logger.warning("Discarding unsupported protocol relation: %s", error)
            continue
        valid_relations.append(relation)
    return {"schema_version": "1.0", "items": valid_items,
            "relations": valid_relations}


def source_span(task: dict, ref: str, quote: str) -> dict:
    span = {
        "source_system": "task_outcome",
        "task_id": str(task["id"]),
        "outcome_sha256": task["summary_sha256"],
        "source_ref": ref,
        "quote": quote,
    }
    if ref.startswith("message:"):
        span["message_id"] = ref.removeprefix("message:")
    return span


def insert_knowledge(db, task: dict, local_id: str, kind: str, title: str,
                     body: str, ref: str, quote: str) -> UUID:
    item_id = uuid5(NAMESPACE_URL, f"sfera:outcome:{task['id']}:{local_id}")
    span = source_span(task, ref, quote)
    message_ids = [span["message_id"]] if "message_id" in span else []
    content_sha = digest(json.dumps({
        "kind": kind, "title": title, "body": body, "span": span,
    }, ensure_ascii=False, sort_keys=True))
    row = db.execute(
        """INSERT INTO galactica_autonomy.knowledge
           (id,project_id,kind,title,body,memory_layer,epistemic_status,
            source_message_ids,source_spans,source_task_ids,tags,content_sha256)
           VALUES (%s,%s,%s,%s,%s,'medium','agent_inference',%s,%s,%s,%s,%s)
           ON CONFLICT (id) DO NOTHING RETURNING id""",
        (item_id, PROJECT_ID, kind, title, body, message_ids, Jsonb([span]),
         [task["id"]], ["outcome_protocol"], content_sha),
    ).fetchone()
    if row is None:
        existing = db.execute(
            "SELECT content_sha256 FROM galactica_autonomy.knowledge WHERE id=%s",
            (item_id,),
        ).fetchone()
        if not existing or existing["content_sha256"] != content_sha:
            raise ValueError("Outcome knowledge ID conflict")
    for target in ("qdrant", "neo4j"):
        db.execute(
            """INSERT INTO galactica_autonomy.projection_outbox
               (entity_type,entity_id,entity_version,target,operation)
               VALUES ('knowledge',%s,1,%s,'upsert') ON CONFLICT DO NOTHING""",
            (item_id, target),
        )
    return item_id


def insert_relation(db, source_id: UUID, target_id: UUID, relation_type: str,
                    message_id: str | None) -> None:
    evidence = [message_id] if message_id else []
    row = db.execute(
        """INSERT INTO galactica_autonomy.relations
           (source_id,target_id,relation_type,evidence_message_ids,
            verification_status)
           VALUES (%s,%s,%s,%s,'model_proposed')
           ON CONFLICT (source_id,target_id,relation_type) DO NOTHING
           RETURNING id""",
        (source_id, target_id, relation_type, evidence),
    ).fetchone()
    if row:
        db.execute(
            """INSERT INTO galactica_autonomy.projection_outbox
               (entity_type,entity_id,entity_version,target,operation)
               VALUES ('relation',%s,1,'neo4j','upsert') ON CONFLICT DO NOTHING""",
            (row["id"],),
        )


def apply(job: dict, task: dict, protocol: dict, model: str,
          source_map: dict[str, str]) -> dict:
    result_text = source_map["summary.result"].strip()
    if len(result_text) > 1200:
        result_text = result_text[:1200]
    if len(result_text) < 6:
        raise ValueError("Task outcome result is too short")
    with connect() as db:
        with db.transaction():
            current = db.execute(
                "SELECT summary_sha256 FROM galactica_autonomy.tasks WHERE id=%s FOR UPDATE",
                (task["id"],),
            ).fetchone()
            if current["summary_sha256"] != job["summary_sha256"]:
                raise ValueError("Task outcome changed during protocolization")
            outcome_id = insert_knowledge(
                db, task, "outcome", "task_insight",
                ("Итог задачи: " + task["request_text"].strip())[:180],
                result_text, "summary.result", result_text,
            )
            ids = {"outcome": outcome_id}
            for item in protocol["items"]:
                ids[item["local_id"]] = insert_knowledge(
                    db, task, item["local_id"], item["kind"], item["title"].strip(),
                    item["body"].strip(), item["source_ref"], item["evidence_quote"],
                )
                ref = item["source_ref"]
                insert_relation(
                    db, outcome_id, ids[item["local_id"]], "ASSOCIATED_WITH",
                    ref.removeprefix("message:") if ref.startswith("message:") else None,
                )
            for relation in protocol["relations"]:
                ref = relation["evidence_ref"]
                insert_relation(
                    db, ids[relation["source_ref"]], ids[relation["target_ref"]],
                    relation["relation_type"],
                    ref.removeprefix("message:") if ref.startswith("message:") else None,
                )
            db.execute(
                """INSERT INTO galactica_autonomy.outcome_protocols
                   (task_id,summary_sha256,model,protocol,validation)
                   VALUES (%s,%s,%s,%s,%s) ON CONFLICT (task_id) DO NOTHING""",
                (task["id"], job["summary_sha256"], model, Jsonb(protocol),
                 Jsonb({"source_refs_checked": True, "items": len(ids),
                        "relations": len(protocol["relations"])})),
            )
            db.execute(
                """UPDATE galactica_autonomy.outcome_protocol_jobs SET
                   status='succeeded',processed_at=now(),locked_at=NULL,
                   last_error=NULL,model=%s WHERE task_id=%s""",
                (model, task["id"]),
            )
    return {"task_id": str(task["id"]), "items": len(ids),
            "model_relations": len(protocol["relations"]), "model": model}


def fail(job: dict, error: Exception) -> None:
    delay = min(300, 2 ** min(job["attempts"], 8))
    with connect() as db:
        with db.transaction():
            db.execute(
                """UPDATE galactica_autonomy.outcome_protocol_jobs SET
                   status=%s,next_retry_at=now()+(%s * interval '1 second'),
                   locked_at=NULL,last_error=%s WHERE task_id=%s""",
                ("dead" if job["attempts"] >= job["max_attempts"] else "failed",
                 delay, f"{type(error).__name__}: {str(error)[:350]}", job["task_id"]),
            )


def recover_stale() -> int:
    with connect() as db:
        with db.transaction():
            return db.execute(
                """UPDATE galactica_autonomy.outcome_protocol_jobs SET
                   status=CASE WHEN attempts>=max_attempts THEN 'dead' ELSE 'failed' END,
                   locked_at=NULL,next_retry_at=now(),
                   last_error='Recovered stale protocol lock'
                   WHERE status='processing' AND locked_at<now()-interval '10 minutes'"""
            ).rowcount


def one() -> dict | None:
    job = claim()
    if job is None:
        return None
    try:
        task, messages = load_task(job["task_id"])
        source_map = safe_sources(task, messages)
        protocol, model = generate(task, source_map)
        protocol = validate(protocol, source_map)
        return apply(job, task, protocol, model, source_map)
    except Exception as error:
        logger.exception("Outcome protocol failed for task %s", job["task_id"])
        fail(job, error)
        return {"task_id": str(job["task_id"]), "status": "failed",
                "error_type": type(error).__name__}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    recover_stale()
    if args.once:
        print(json.dumps(one() or {"status": "no_work"}, ensure_ascii=False))
        return
    while True:
        result = one()
        if result:
            logger.info("Outcome protocol task=%s status=%s items=%s",
                        result["task_id"], result.get("status", "succeeded"),
                        result.get("items"))
        else:
            recovered = recover_stale()
            if recovered:
                logger.warning("Recovered %s stale outcome protocol jobs", recovered)
            time.sleep(5)


if __name__ == "__main__":
    main()
