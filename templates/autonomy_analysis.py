"""Bounded nightly export and validated ingestion of Codex CLI results."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from uuid import UUID, NAMESPACE_URL, uuid5

from psycopg.types.json import Jsonb

from autonomy_store import PROJECT_ID, SECRET_PATTERNS, connect, digest


MAX_EVENTS = 30
MAX_DOCUMENT_CHARS = 6000
MAX_MESSAGE_CHARS = 16000
ALLOWED_KINDS = frozenset({
    "concept", "procedure", "decision", "requirement", "constraint",
    "lesson", "failure", "alternative",
})
ALLOWED_RELATIONS = frozenset({
    "SUPPORTS", "CONTRADICTS", "SUPERSEDES", "REQUIRES", "ASSOCIATED_WITH",
})


def begin() -> dict:
    with connect() as database:
        with database.transaction():
            database.execute("SELECT pg_advisory_xact_lock(74641624)")
            cursor = database.execute(
                """SELECT COALESCE(MAX(cursor_end),0) AS cursor
                   FROM galactica_autonomy.nightly_runs WHERE status='succeeded'"""
            ).fetchone()["cursor"]
            pending = database.execute(
                """SELECT * FROM galactica_autonomy.nightly_runs
                   WHERE cursor_start=%s AND status IN ('running','failed')
                   ORDER BY started_at DESC LIMIT 1 FOR UPDATE""",
                (cursor,),
            ).fetchone()
            if pending:
                database.execute(
                    """UPDATE galactica_autonomy.nightly_runs
                       SET status='running',error_text=NULL WHERE id=%s""",
                    (pending["id"],),
                )
                run_id = pending["id"]
                boundary = pending["boundary_seq"]
            else:
                maximum = database.execute(
                    "SELECT COALESCE(MAX(seq),0) AS maximum FROM galactica_autonomy.change_events"
                ).fetchone()["maximum"]
                if maximum <= cursor:
                    return {"status": "no_work", "cursor": cursor}
                boundary = min(maximum, cursor + MAX_EVENTS)
                run_id = database.execute(
                    """INSERT INTO galactica_autonomy.nightly_runs
                       (boundary_seq,cursor_start,status,report)
                       VALUES (%s,%s,'running',%s) RETURNING id""",
                    (boundary, cursor, Jsonb({"available_max_seq": maximum})),
                ).fetchone()["id"]
    payload = export(run_id)
    return payload


def export(run_id: UUID) -> dict:
    with connect() as database:
        run = database.execute(
            "SELECT * FROM galactica_autonomy.nightly_runs WHERE id=%s", (run_id,)
        ).fetchone()
        if not run:
            raise ValueError("Unknown nightly run")
        events = database.execute(
            """SELECT e.*,d.relative_path,v.content AS after_content,
                      b.content AS before_content,t.summary,t.status AS task_status
               FROM galactica_autonomy.change_events e
               JOIN galactica_autonomy.documents d ON d.id=e.document_id
               JOIN galactica_autonomy.document_versions v
                 ON v.document_id=e.document_id AND v.version=e.after_version
               LEFT JOIN galactica_autonomy.document_versions b
                 ON b.document_id=e.document_id AND b.version=e.before_version
               LEFT JOIN galactica_autonomy.tasks t ON t.id=e.task_id
               WHERE e.seq>%s AND e.seq<=%s ORDER BY e.seq""",
            (run["cursor_start"], run["boundary_seq"]),
        ).fetchall()
        message_ids = sorted({value for event in events for value in event["source_message_ids"]})
        messages = database.execute(
            """SELECT id,role,body,completeness,body_sha256,source_system,
                      source_message_id FROM galactica_autonomy.messages
               WHERE id=ANY(%s) ORDER BY id""",
            (message_ids,),
        ).fetchall() if message_ids else []
    event_rows = []
    for event in events:
        after = event["after_content"]
        before = event["before_content"] or ""
        sourced = bool(event["source_message_ids"])
        event_rows.append({
            "seq": event["seq"],
            "document_id": str(event["document_id"]),
            "path": event["relative_path"],
            "before_sha256": event["before_sha256"],
            "after_sha256": event["after_sha256"],
            "before_version": event["before_version"],
            "after_version": event["after_version"],
            "after_content": after[:MAX_DOCUMENT_CHARS] if sourced else "",
            "after_truncated": sourced and len(after) > MAX_DOCUMENT_CHARS,
            "before_content": before[:MAX_DOCUMENT_CHARS] if sourced else "",
            "before_truncated": sourced and len(before) > MAX_DOCUMENT_CHARS,
            "skip_reason": None if sourced else "no_exact_message_source",
            "source_message_ids": event["source_message_ids"],
            "task_id": str(event["task_id"]) if event["task_id"] else None,
            "task_status": event["task_status"],
            "task_summary": event["summary"],
            "status": event["status"],
        })
    message_rows = [{
        "id": row["id"],
        "role": row["role"],
        "body": row["body"][:MAX_MESSAGE_CHARS],
        "truncated": len(row["body"]) > MAX_MESSAGE_CHARS,
        "completeness": row["completeness"],
        "body_sha256": row["body_sha256"],
        "source_system": row["source_system"],
        "source_message_id": row["source_message_id"],
    } for row in messages]
    return {
        "status": "ready",
        "run_id": str(run_id),
        "cursor_start": run["cursor_start"],
        "boundary_seq": run["boundary_seq"],
        "events": event_rows,
        "messages": message_rows,
        "source_scope": "CLIENT daughter only",
    }


def checked_evidence(database, entries: list[dict], allowed_ids: set[str]) -> tuple[list[str], list[dict]]:
    if not isinstance(entries, list) or not entries:
        raise ValueError("Every knowledge item requires source evidence")
    ids: list[str] = []
    spans: list[dict] = []
    for entry in entries:
        message_id = str(entry.get("message_id", ""))
        quote = str(entry.get("quote", ""))
        if message_id not in allowed_ids or not 8 <= len(quote) <= 1000:
            raise ValueError("Evidence message or quote is invalid")
        row = database.execute(
            "SELECT body,completeness FROM galactica_autonomy.messages WHERE id=%s",
            (message_id,),
        ).fetchone()
        if not row or row["completeness"] != "exact":
            raise ValueError("Only exact sourced messages may support a quote")
        start = row["body"].find(quote)
        if start < 0:
            raise ValueError("Quote is not an exact source substring")
        quote = quote[:500]
        ids.append(message_id)
        spans.append({"message_id": message_id, "start": start,
                      "end": start + len(quote), "quote": quote})
    return sorted(set(ids)), spans


def checked_task_ids(database, ids: list[str], allowed: set[UUID]) -> list[UUID]:
    values = [UUID(str(value)) for value in ids]
    if not values or not set(values).issubset(allowed):
        raise ValueError("Knowledge must cite a task in the processed events")
    for value in values:
        if not database.execute(
            "SELECT 1 FROM galactica_autonomy.tasks WHERE id=%s", (value,)
        ).fetchone():
            raise ValueError("Unknown source task")
    return list(dict.fromkeys(values))


def checked_verifications(database, task_ids: list[UUID], refs: list[str]) -> list[str]:
    allowed: set[str] = set()
    for task_id in task_ids:
        row = database.execute(
            "SELECT summary FROM galactica_autonomy.tasks WHERE id=%s", (task_id,)
        ).fetchone()
        for check in (row["summary"] or {}).get("checks", []) if row else []:
            if isinstance(check, dict) and check.get("source_ref"):
                allowed.add(str(check["source_ref"]))
    if not set(refs).issubset(allowed):
        raise ValueError("Verification reference is absent from task checks")
    return list(dict.fromkeys(refs))


def validate_item(database, item: dict, allowed_messages: set[str], allowed_tasks: set[UUID]) -> dict:
    local_id = str(item.get("local_id", ""))
    kind = str(item.get("kind", ""))
    title = str(item.get("title", "")).strip()
    body = str(item.get("body", "")).strip()
    layer = str(item.get("memory_layer", ""))
    epistemic = str(item.get("epistemic_status", ""))
    tags = item.get("tags", [])
    if not re_local_id(local_id) or kind not in ALLOWED_KINDS:
        raise ValueError("Invalid local ID or knowledge kind")
    if not title or not body or len(title) > 180 or len(body) > 1200:
        raise ValueError("Knowledge text is empty or too long")
    if any(pattern.search(title + "\n" + body) for pattern in SECRET_PATTERNS):
        raise ValueError("Knowledge appears to contain a secret")
    if layer not in {"medium", "long"} or epistemic not in {
        "user_assertion", "agent_inference", "verified_fact", "proposal", "accepted_decision"
    }:
        raise ValueError("Invalid memory or epistemic status")
    if not isinstance(tags, list) or len(tags) > 10 or any(not isinstance(t, str) or len(t) > 50 for t in tags):
        raise ValueError("Invalid tags")
    source_ids, spans = checked_evidence(database, item.get("evidence", []), allowed_messages)
    task_ids = checked_task_ids(database, item.get("source_task_ids", []), allowed_tasks)
    refs = checked_verifications(database, task_ids, item.get("verification_refs", []))
    if layer == "long" and epistemic not in {"verified_fact", "accepted_decision"}:
        raise ValueError("Unverified knowledge cannot enter long-term memory")
    if epistemic == "verified_fact" and not refs:
        raise ValueError("Verified facts require task check references")
    if epistemic == "accepted_decision":
        roles = database.execute(
            "SELECT role FROM galactica_autonomy.messages WHERE id=ANY(%s)",
            (source_ids,),
        ).fetchall()
        if not any(row["role"] == "user" for row in roles):
            raise ValueError("Accepted decisions require a user source")
    return {
        "local_id": local_id, "kind": kind, "title": title, "body": body,
        "memory_layer": layer, "epistemic_status": epistemic,
        "tags": list(dict.fromkeys(tags)), "source_message_ids": source_ids,
        "source_spans": spans, "source_task_ids": task_ids,
        "verification_refs": refs,
    }


def re_local_id(value: str) -> bool:
    return bool(value) and len(value) <= 80 and all(c.isalnum() or c in "_-" for c in value)


def validate_candidate(database, candidate: dict, allowed_messages: set[str],
                       allowed_tasks: set[UUID]) -> tuple[dict, UUID]:
    key = str(candidate.get("key", ""))
    title = str(candidate.get("title", "")).strip()
    scope = str(candidate.get("scope", "")).strip()
    steps = candidate.get("steps", [])
    prerequisites = candidate.get("prerequisites", [])
    limitations = candidate.get("limitations", [])
    tools = candidate.get("tools", [])
    tags = candidate.get("tags", [])
    if (not 3 <= len(key) <= 64 or key[0] not in "abcdefghijklmnopqrstuvwxyz" or
            any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in key)):
        raise ValueError("Invalid skill candidate key")
    if not title or not scope or len(title) > 180 or len(scope) > 300:
        raise ValueError("Invalid skill candidate title or scope")
    for name, value, maximum in (("steps", steps, 8), ("prerequisites", prerequisites, 8),
                                 ("limitations", limitations, 8), ("tools", tools, 8),
                                 ("tags", tags, 10)):
        if not isinstance(value, list) or len(value) > maximum or any(
            not isinstance(item, str) or not item.strip() or len(item) > 300 for item in value
        ):
            raise ValueError(f"Invalid skill candidate {name}")
    if not steps or not tools:
        raise ValueError("Skill candidate requires steps and tools")
    if any(pattern.search("\n".join([title, scope, *steps, *prerequisites, *limitations, *tools]))
           for pattern in SECRET_PATTERNS):
        raise ValueError("Skill candidate appears to contain a secret")
    task_id = UUID(str(candidate.get("source_task_id")))
    if task_id not in allowed_tasks:
        raise ValueError("Skill candidate must cite a processed task")
    task = database.execute(
        "SELECT status FROM galactica_autonomy.tasks WHERE id=%s", (task_id,)
    ).fetchone()
    if not task or task["status"] != "succeeded":
        raise ValueError("Skill candidate requires a successful task")
    refs = checked_verifications(database, [task_id], candidate.get("verification_refs", []))
    if not refs:
        raise ValueError("Skill candidate requires task checks")
    source_ids, _ = checked_evidence(database, candidate.get("evidence", []), allowed_messages)
    spec = {"key": key, "title": title, "scope": scope, "tags": tags,
            "steps": steps, "prerequisites": prerequisites, "tools": tools,
            "limitations": limitations, "source_message_ids": source_ids,
            "verification_refs": refs}
    return spec, task_id


def apply(run_id: UUID, result_file: Path) -> dict:
    result = json.loads(result_file.read_text(encoding="utf-8"))
    if not isinstance(result, dict) or not isinstance(result.get("items"), list) or not isinstance(result.get("relations"), list) or not isinstance(result.get("skill_candidates", []), list):
        raise ValueError("Analysis must contain items and relations arrays")
    if len(result["items"]) > 30 or len(result["relations"]) > 60 or len(result.get("skill_candidates", [])) > 3:
        raise ValueError("Analysis exceeds batch limits")
    with connect() as database:
        with database.transaction():
            database.execute("SELECT pg_advisory_xact_lock(74641624)")
            run = database.execute(
                "SELECT * FROM galactica_autonomy.nightly_runs WHERE id=%s FOR UPDATE",
                (run_id,),
            ).fetchone()
            if not run:
                raise ValueError("Unknown nightly run")
            if run["status"] == "succeeded":
                return {"run_id": str(run_id), "status": "succeeded", "idempotent": True}
            if run["status"] != "running":
                raise ValueError("Run must be resumed before applying")
            events = database.execute(
                """SELECT task_id,source_message_ids FROM galactica_autonomy.change_events
                   WHERE seq>%s AND seq<=%s""",
                (run["cursor_start"], run["boundary_seq"]),
            ).fetchall()
            allowed_messages = {mid for event in events for mid in event["source_message_ids"]}
            allowed_tasks = {event["task_id"] for event in events if event["task_id"]}
            prepared = [validate_item(database, item, allowed_messages, allowed_tasks)
                        for item in result["items"]]
            local_ids = [item["local_id"] for item in prepared]
            if len(local_ids) != len(set(local_ids)):
                raise ValueError("Duplicate local knowledge IDs")
            item_ids = {local_id: uuid5(NAMESPACE_URL, f"galactica-autonomy:{run_id}:{local_id}")
                        for local_id in local_ids}
            duplicate_count = 0
            for item in prepared:
                item_id = item_ids[item["local_id"]]
                primary = item["source_spans"][0]
                duplicate = database.execute(
                    """SELECT id FROM galactica_autonomy.knowledge
                       WHERE project_id=%s AND kind=%s AND status='active'
                         AND source_spans @> %s
                       ORDER BY created_at,id LIMIT 1""",
                    (PROJECT_ID, item["kind"], Jsonb([{
                        "message_id": primary["message_id"], "start": primary["start"]
                    }])),
                ).fetchone()
                if duplicate:
                    item_ids[item["local_id"]] = duplicate["id"]
                    duplicate_count += 1
                    continue
                content_sha = digest(json.dumps(item, ensure_ascii=False, sort_keys=True, default=str))
                existing = database.execute(
                    "SELECT content_sha256 FROM galactica_autonomy.knowledge WHERE id=%s",
                    (item_id,),
                ).fetchone()
                if existing and existing["content_sha256"] != content_sha:
                    raise ValueError("Knowledge ID collision with changed content")
                if not existing:
                    database.execute(
                        """INSERT INTO galactica_autonomy.knowledge
                           (id,project_id,kind,title,body,memory_layer,epistemic_status,
                            source_message_ids,source_spans,source_task_ids,tags,
                            verification_refs,content_sha256)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (item_id, PROJECT_ID, item["kind"], item["title"], item["body"],
                         item["memory_layer"], item["epistemic_status"],
                         item["source_message_ids"], Jsonb(item["source_spans"]),
                         item["source_task_ids"], item["tags"],
                         item["verification_refs"], content_sha),
                    )
                for target in ("qdrant", "neo4j"):
                    database.execute(
                        """INSERT INTO galactica_autonomy.projection_outbox
                           (entity_type,entity_id,entity_version,target,operation)
                           VALUES ('knowledge',%s,1,%s,'upsert')
                           ON CONFLICT DO NOTHING""",
                        (item_id, target),
                    )
            for relation in result["relations"]:
                source_ref = str(relation.get("source_ref", ""))
                target_ref = str(relation.get("target_ref", ""))
                relation_type = str(relation.get("relation_type", "")).upper()
                if source_ref not in item_ids or target_ref not in item_ids or relation_type not in ALLOWED_RELATIONS:
                    raise ValueError("Relation endpoints or type are invalid")
                evidence_ids, _ = checked_evidence(
                    database, relation.get("evidence", []), allowed_messages
                )
                relation_id = uuid5(
                    NAMESPACE_URL,
                    f"galactica-autonomy:{run_id}:relation:{source_ref}:{relation_type}:{target_ref}",
                )
                database.execute(
                    """INSERT INTO galactica_autonomy.relations
                       (id,source_id,target_id,relation_type,evidence_message_ids)
                       VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (relation_id, item_ids[source_ref], item_ids[target_ref],
                     relation_type, evidence_ids),
                )
                database.execute(
                    """INSERT INTO galactica_autonomy.projection_outbox
                       (entity_type,entity_id,entity_version,target,operation)
                       VALUES ('relation',%s,1,'neo4j','upsert')
                       ON CONFLICT DO NOTHING""",
                    (relation_id,),
                )
            candidate_conflicts = 0
            candidate_count = 0
            for candidate in result.get("skill_candidates", []):
                spec, task_id = validate_candidate(database, candidate, allowed_messages, allowed_tasks)
                procedure = {"steps": spec["steps"], "limitations": spec["limitations"]}
                existing = database.execute(
                    "SELECT * FROM galactica_autonomy.skill_candidates WHERE key=%s FOR UPDATE",
                    (spec["key"],),
                ).fetchone()
                if existing and (existing["title"] != spec["title"] or
                                 existing["scope"] != spec["scope"] or
                                 existing["tags"] != spec["tags"] or
                                 existing["procedure"] != procedure or
                                 existing["prerequisites"] != spec["prerequisites"] or
                                 existing["tools"] != spec["tools"]):
                    candidate_conflicts += 1
                    continue
                if not existing:
                    database.execute(
                        """INSERT INTO galactica_autonomy.skill_candidates
                           (key,title,scope,tags,procedure,prerequisites,tools)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        (spec["key"], spec["title"], spec["scope"], spec["tags"],
                         Jsonb(procedure), Jsonb(spec["prerequisites"]), spec["tools"]),
                    )
                    database.execute(
                        """INSERT INTO galactica_autonomy.skill_versions
                           (candidate_key,version,snapshot,status)
                           VALUES (%s,1,%s,'candidate')""",
                        (spec["key"], Jsonb(spec)),
                    )
                database.execute(
                    """INSERT INTO galactica_autonomy.skill_applications
                       (candidate_key,task_id,succeeded,test_refs)
                       VALUES (%s,%s,true,%s) ON CONFLICT DO NOTHING""",
                    (spec["key"], task_id, spec["verification_refs"]),
                )
                candidate_count += 1
            report = {
                "events": len(events), "knowledge_items": len(prepared) - duplicate_count,
                "deduplicated": duplicate_count,
                "relations": len(result["relations"]),
                "skill_candidates": candidate_count,
                "candidate_conflicts": candidate_conflicts,
                "events_without_message_sources": sum(not e["source_message_ids"] for e in events),
                "projection_status": "queued",
            }
            database.execute(
                """UPDATE galactica_autonomy.nightly_runs SET
                   status='succeeded',cursor_end=boundary_seq,processed=%s,
                   skipped=%s,needs_review=%s,report=%s,finished_at=now() WHERE id=%s""",
                (len(events), report["events_without_message_sources"],
                 candidate_conflicts, Jsonb(report), run_id),
            )
    return {"run_id": str(run_id), "status": "succeeded", **report}


def fail(run_id: UUID, message: str) -> dict:
    with connect() as database:
        with database.transaction():
            database.execute(
                """UPDATE galactica_autonomy.nightly_runs
                   SET status='failed',error_text=%s,finished_at=now()
                   WHERE id=%s AND status='running'""",
                (message[:1000], run_id),
            )
    return {"run_id": str(run_id), "status": "failed"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["begin", "export", "apply", "fail"])
    parser.add_argument("--run-id", type=UUID)
    parser.add_argument("--result-file", type=Path)
    parser.add_argument("--error")
    args = parser.parse_args()
    if args.command == "begin":
        value = begin()
    elif args.command == "export":
        value = export(args.run_id)
    elif args.command == "apply":
        value = apply(args.run_id, args.result_file)
    else:
        value = fail(args.run_id, args.error or "unknown error")
    print(json.dumps(value, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
