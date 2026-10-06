"""Explicit, provenance-checked import of reviewed knowledge into CLIENT.

This command never contacts the source system. A caller supplies the export.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from psycopg.types.json import Jsonb

from autonomy_store import PROJECT_ID, SECRET_PATTERNS, connect, digest


def import_one(path: Path) -> dict:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    source_system = envelope.get("source_system")
    if source_system not in ("galactica_parent", "trend_runtime", "codex_local"):
        raise ValueError("Unsupported source system")
    source_project_id = str(envelope.get("source_project_id", ""))
    try:
        UUID(source_project_id)
    except (ValueError, TypeError) as exc:
        raise ValueError("A real source project ID is required") from exc
    if source_project_id != str(PROJECT_ID) and envelope.get("source_scope") != "general_reusable":
        raise ValueError("Cross-project import requires explicit general_reusable scope")
    if source_system == "trend_runtime" and source_project_id != str(PROJECT_ID):
        raise ValueError("TREND runtime import must belong to CLIENT")
    if source_system == "codex_local" and source_project_id != str(PROJECT_ID):
        raise ValueError("Local Codex thread import must belong to CLIENT")
    source_locator = str(envelope.get("source_locator", ""))
    if source_system == "trend_runtime" and not source_locator:
        raise ValueError("TREND runtime asset locator is required")
    if source_system == "codex_local":
        if not source_locator.startswith("codex://threads/"):
            raise ValueError("Local Codex thread locator is required")
        try:
            UUID(source_locator.removeprefix("codex://threads/"))
        except ValueError as exc:
            raise ValueError("Local Codex thread locator must contain a UUID") from exc
    source_title = str(envelope.get("source_title", "")).strip()
    source_date = str(envelope.get("source_date", "")).strip()
    if len(source_title) > 180 or len(source_date) > 40:
        raise ValueError("Source label is too long")
    source_id = str(envelope.get("source_canonical_id", ""))
    item = envelope.get("knowledge")
    if not source_id or not isinstance(item, dict):
        raise ValueError("Canonical source ID and knowledge are required")
    serialized = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    source_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    if source_hash != envelope.get("source_hash"):
        raise ValueError("Export hash mismatch")
    title = str(item.get("title", "")).strip()
    body = str(item.get("body", "")).strip()
    kind = str(item.get("kind", "concept"))
    tags = item.get("tags", [])
    if not title or not body or len(title) > 180 or len(body) > 1200:
        raise ValueError("Imported knowledge text invalid")
    if not isinstance(tags, list) or any(not isinstance(t, str) or len(t) > 50 for t in tags):
        raise ValueError("Imported tags invalid")
    if any(p.search(title + "\n" + body) for p in SECRET_PATTERNS):
        raise ValueError("Imported knowledge appears to contain a secret")
    imported_id = uuid5(NAMESPACE_URL, f"galactica-autonomy:import:{source_system}:{source_id}:{source_hash}")
    content_sha = digest(json.dumps({"title": title, "body": body, "kind": kind,
                                     "tags": tags, "source_hash": source_hash},
                                    ensure_ascii=False, sort_keys=True))
    with connect() as db:
        with db.transaction():
            existing = db.execute(
                """SELECT imported_knowledge_id FROM galactica_autonomy.imports
                   WHERE source_system=%s AND source_canonical_id=%s
                     AND source_hash=%s""", (source_system, source_id, source_hash)
            ).fetchone()
            if existing:
                return {"id": str(existing["imported_knowledge_id"]), "status": "already_imported"}
            db.execute(
                """INSERT INTO galactica_autonomy.knowledge
                   (id,project_id,kind,title,body,memory_layer,epistemic_status,
                    source_spans,tags,status,content_sha256)
                   VALUES (%s,%s,%s,%s,%s,'medium','agent_inference',%s,%s,'review',%s)""",
                (imported_id, PROJECT_ID, kind, title, body,
                 Jsonb([{"source_system": source_system, "source_project_id": source_project_id,
                         "source_scope": envelope.get("source_scope", "project"),
                         "source_canonical_id": source_id, "source_hash": source_hash,
                         "source_locator": source_locator,
                         "source_revision": str(envelope.get("source_revision", "")),
                         "source_title": source_title, "source_date": source_date}]),
                 tags, content_sha),
            )
            db.execute(
                """INSERT INTO galactica_autonomy.imports
                   (source_system,source_canonical_id,imported_knowledge_id,source_hash)
                   VALUES (%s,%s,%s,%s)""",
                (source_system, source_id, imported_id, source_hash),
            )
    return {"id": str(imported_id), "status": "review", "source_hash": source_hash}


def accept(imported_id: UUID) -> dict:
    with connect() as db:
        with db.transaction():
            row = db.execute(
                """SELECT k.status,k.version FROM galactica_autonomy.knowledge k
                   JOIN galactica_autonomy.imports i ON i.imported_knowledge_id=k.id
                   WHERE k.id=%s FOR UPDATE OF k""", (imported_id,)
            ).fetchone()
            if not row:
                raise ValueError("Unknown imported knowledge")
            version = row["version"]
            if row["status"] == "review":
                version += 1
                db.execute(
                    """UPDATE galactica_autonomy.knowledge SET status='active',version=%s,
                       updated_at=now() WHERE id=%s""", (version, imported_id),
                )
            elif row["status"] != "active":
                raise ValueError("Imported knowledge is no longer reviewable")
            for target in ("qdrant", "neo4j"):
                db.execute(
                    """INSERT INTO galactica_autonomy.projection_outbox
                       (entity_type,entity_id,entity_version,target,operation)
                       VALUES ('knowledge',%s,%s,%s,'upsert') ON CONFLICT DO NOTHING""",
                    (imported_id, version, target),
                )
    return {"id": str(imported_id), "status": "active", "version": version}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("import")
    p.add_argument("--file", type=Path, required=True)
    p = sub.add_parser("accept")
    p.add_argument("--id", type=UUID, required=True)
    args = parser.parse_args()
    result = import_one(args.file) if args.command == "import" else accept(args.id)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
