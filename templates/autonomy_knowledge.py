"""Journaled canonical knowledge edits, withdrawal, and replacement."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import UUID

from psycopg.types.json import Jsonb

from autonomy_store import PROJECT_ID, SECRET_PATTERNS, connect, digest


def queue(db, item_id: UUID, version: int, operation: str) -> None:
    for target in ("qdrant", "neo4j"):
        db.execute(
            """INSERT INTO galactica_autonomy.projection_outbox
               (entity_type,entity_id,entity_version,target,operation)
               VALUES ('knowledge',%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (item_id, version, target, operation),
        )


def snapshot(db, row: dict, action: str, source_message_id: str) -> None:
    db.execute(
        """INSERT INTO galactica_autonomy.knowledge_revisions
           (knowledge_id,version,action,snapshot,source_message_id)
           VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
        (row["id"], row["version"], action,
         Jsonb(json.loads(json.dumps(dict(row), default=str))), source_message_id),
    )


def verify_source(db, message_id: str) -> None:
    row = db.execute(
        "SELECT completeness FROM galactica_autonomy.messages WHERE id=%s", (message_id,)
    ).fetchone()
    if not row or row["completeness"] != "exact":
        raise ValueError("Knowledge mutation requires an exact source message")


def edit(item_id: UUID, expected_sha: str, data: dict, message_id: str) -> dict:
    title = str(data.get("title", "")).strip()
    body = str(data.get("body", "")).strip()
    tags = data.get("tags", [])
    if not title or not body or len(title) > 180 or len(body) > 1200 or not isinstance(tags, list):
        raise ValueError("Invalid knowledge edit")
    if any(pattern.search(title + "\n" + body) for pattern in SECRET_PATTERNS):
        raise ValueError("Knowledge edit appears to contain a secret")
    with connect() as db:
        with db.transaction():
            verify_source(db, message_id)
            row = db.execute(
                "SELECT * FROM galactica_autonomy.knowledge WHERE id=%s FOR UPDATE", (item_id,)
            ).fetchone()
            if not row or row["project_id"] != PROJECT_ID or row["status"] != "active":
                raise ValueError("Unknown or inactive knowledge")
            if row["content_sha256"] != expected_sha:
                raise ValueError("Knowledge version conflict")
            snapshot(db, row, "before_edit", message_id)
            new_sha = digest(json.dumps({"title": title, "body": body, "tags": tags,
                                         "prior_sha": expected_sha},
                                        ensure_ascii=False, sort_keys=True))
            version = row["version"] + 1
            db.execute(
                """UPDATE galactica_autonomy.knowledge SET title=%s,body=%s,tags=%s,
                   version=%s,content_sha256=%s,updated_at=now() WHERE id=%s""",
                (title, body, tags, version, new_sha, item_id),
            )
            queue(db, item_id, version, "upsert")
    return {"id": str(item_id), "version": version, "sha256": new_sha}


def retire(item_id: UUID, message_id: str, replacement: UUID | None) -> dict:
    with connect() as db:
        with db.transaction():
            verify_source(db, message_id)
            row = db.execute(
                "SELECT * FROM galactica_autonomy.knowledge WHERE id=%s FOR UPDATE", (item_id,)
            ).fetchone()
            if not row or row["project_id"] != PROJECT_ID:
                raise ValueError("Unknown CLIENT knowledge")
            if replacement:
                if item_id == replacement:
                    raise ValueError("Replacement must be a different ID")
                target = db.execute(
                    "SELECT status,project_id FROM galactica_autonomy.knowledge WHERE id=%s",
                    (replacement,),
                ).fetchone()
                if not target or target["project_id"] != PROJECT_ID or target["status"] != "active":
                    raise ValueError("Replacement must be active CLIENT knowledge")
            new_status = "superseded" if replacement else "review"
            if row["status"] == new_status and row["superseded_by"] == replacement:
                return {"id": str(item_id), "version": row["version"], "status": new_status,
                        "idempotent": True}
            if row["status"] != "active":
                raise ValueError("Knowledge is already inactive")
            snapshot(db, row, "before_retire", message_id)
            version = row["version"] + 1
            db.execute(
                """UPDATE galactica_autonomy.knowledge SET status=%s,superseded_by=%s,
                   version=%s,updated_at=now() WHERE id=%s""",
                (new_status, replacement, version, item_id),
            )
            queue(db, item_id, version, "delete")
            relations = db.execute(
                """UPDATE galactica_autonomy.relations SET status='superseded'
                   WHERE status='active' AND (source_id=%s OR target_id=%s) RETURNING id""",
                (item_id, item_id),
            ).fetchall()
            for relation in relations:
                db.execute(
                    """INSERT INTO galactica_autonomy.projection_outbox
                       (entity_type,entity_id,entity_version,target,operation)
                       VALUES ('relation',%s,1,'neo4j','delete') ON CONFLICT DO NOTHING""",
                    (relation["id"],),
                )
    return {"id": str(item_id), "version": version, "status": new_status,
            "replacement": str(replacement) if replacement else None}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("withdraw", "supersede"):
        p = sub.add_parser(name)
        p.add_argument("--id", type=UUID, required=True)
        p.add_argument("--message-id", required=True)
        if name == "supersede":
            p.add_argument("--replacement", type=UUID, required=True)
    p = sub.add_parser("edit")
    p.add_argument("--id", type=UUID, required=True)
    p.add_argument("--expected-sha", required=True)
    p.add_argument("--data-file", type=Path, required=True)
    p.add_argument("--message-id", required=True)
    args = parser.parse_args()
    if args.command == "edit":
        result = edit(args.id, args.expected_sha,
                      json.loads(args.data_file.read_text(encoding="utf-8")), args.message_id)
    else:
        result = retire(args.id, args.message_id,
                        args.replacement if args.command == "supersede" else None)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
