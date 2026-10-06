"""Canonical task, message, and Markdown store for the VPS daughter.

All file changes require an expected SHA-256 and are journaled with snapshots.
The command accepts only explicit message sources; it never reconstructs quotes.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


PROJECT_ID = UUID(os.getenv("CLIENT_PROJECT_ID", "@@PROJECT_ID@@"))
ROOT = Path(os.getenv("GALACTICA_KNOWLEDGE_ROOT", "/knowledge")).resolve()
NAMESPACE = uuid5(NAMESPACE_URL, "galactica:sfera:vps-documents")
REQUIRED_SUMMARY = frozenset({
    "request", "criteria", "actions", "result", "decisions", "rejected_options",
    "skills_used", "checks", "changed_files", "limitations", "next_step",
    "message_refs", "artifact_refs",
})
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:OPENSSH|RSA|EC|PRIVATE) PRIVATE KEY-----"),
    re.compile(r"(?i)(?:password|api[_-]?key|secret)\s*[:=]\s*\S{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def connect() -> psycopg.Connection:
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    return psycopg.connect(url, row_factory=dict_row)


def safe_path(relative: str) -> Path:
    path = (ROOT / relative).resolve()
    if ROOT not in path.parents or path.suffix.lower() != ".md":
        raise ValueError("Markdown path must stay inside the knowledge root")
    return path


def document_id(logical_key: str) -> UUID:
    return uuid5(NAMESPACE, logical_key)


def parse_frontmatter_id(content: str) -> UUID:
    if not content.startswith("---\n"):
        raise ValueError("Markdown must begin with YAML frontmatter")
    header = content.split("\n---\n", 1)[0]
    match = re.search(r"(?m)^galactica_id:\s*['\"]?([0-9a-fA-F-]{36})", header)
    if not match:
        raise ValueError("Markdown frontmatter lacks galactica_id")
    return UUID(match.group(1))


@contextmanager
def document_lock(doc_id: UUID):
    locks = ROOT / ".locks"
    locks.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (locks / f"{doc_id}.lock").open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def seed_documents() -> dict[str, int]:
    added = 0
    verified = 0
    with connect() as database:
        for path in sorted(ROOT.rglob("*.md")):
            if ".locks" in path.parts:
                continue
            relative = path.relative_to(ROOT).as_posix()
            content = path.read_text(encoding="utf-8")
            if file_digest(path) != digest(content):
                atomic_write(path, content)
            doc_id = parse_frontmatter_id(content)
            logical_key = relative.removesuffix(".md").lower()
            expected = document_id(logical_key)
            if doc_id != expected:
                raise ValueError(f"Unexpected document ID in {relative}")
            sha = digest(content)
            with database.transaction():
                row = database.execute(
                    "SELECT * FROM galactica_autonomy.documents WHERE id=%s FOR UPDATE",
                    (doc_id,),
                ).fetchone()
                if row:
                    if row["sha256"] != sha or row["relative_path"] != relative:
                        raise ValueError(f"Existing document changed: {relative}; run reconcile")
                    verified += 1
                    continue
                database.execute(
                    """INSERT INTO galactica_autonomy.documents
                       (id,project_id,logical_key,relative_path,version,sha256,content)
                       VALUES (%s,%s,%s,%s,1,%s,%s)""",
                    (doc_id, PROJECT_ID, logical_key, relative, sha, content),
                )
                database.execute(
                    """INSERT INTO galactica_autonomy.document_versions
                       (document_id,version,sha256,content) VALUES (%s,1,%s,%s)""",
                    (doc_id, sha, content),
                )
                database.execute(
                    """INSERT INTO galactica_autonomy.change_events
                       (agent_id,project_id,document_id,after_sha256,after_version,status)
                       VALUES ('bootstrap',%s,%s,%s,1,'applied')""",
                    (PROJECT_ID, doc_id, sha),
                )
                added += 1
    return {"added": added, "verified": verified}


def start_task(task_id: UUID, agent_id: str, request_file: Path, criteria_file: Path) -> dict:
    request = request_file.read_text(encoding="utf-8")
    criteria = json.loads(criteria_file.read_text(encoding="utf-8"))
    if not request.strip() or not isinstance(criteria, list):
        raise ValueError("Task request and criteria are required")
    with connect() as database:
        with database.transaction():
            row = database.execute(
                """INSERT INTO galactica_autonomy.tasks
                   (id,project_id,agent_id,request_text,completion_criteria)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO NOTHING RETURNING id""",
                (task_id, PROJECT_ID, agent_id, request, Jsonb(criteria)),
            ).fetchone()
            if row is None:
                existing = database.execute(
                    "SELECT request_text,agent_id FROM galactica_autonomy.tasks WHERE id=%s",
                    (task_id,),
                ).fetchone()
                if existing["request_text"] != request or existing["agent_id"] != agent_id:
                    raise ValueError("Task ID already exists with different source")
    return {"task_id": str(task_id), "status": "active"}


def ingest_message(
    message_id: str, task_id: UUID | None, source_system: str,
    source_message_id: str, role: str, text_file: Path,
    completeness: str, source_locator: str | None, redaction_note: str | None,
) -> dict:
    body = text_file.read_text(encoding="utf-8")
    if not body or any(pattern.search(body) for pattern in SECRET_PATTERNS):
        raise ValueError("Message is empty or appears to contain a secret")
    if completeness == "exact" and redaction_note:
        raise ValueError("A redacted message cannot be marked exact")
    if completeness == "redacted" and not redaction_note:
        raise ValueError("Redaction note is required")
    sha = digest(body)
    with connect() as database:
        with database.transaction():
            row = database.execute(
                """INSERT INTO galactica_autonomy.messages
                   (id,task_id,source_system,source_message_id,role,body,body_sha256,
                    completeness,source_locator,redaction_note)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO NOTHING RETURNING id""",
                (message_id, task_id, source_system, source_message_id, role,
                 body, sha, completeness, source_locator, redaction_note),
            ).fetchone()
            if row is None:
                existing = database.execute(
                    "SELECT body_sha256,source_system,source_message_id FROM galactica_autonomy.messages WHERE id=%s",
                    (message_id,),
                ).fetchone()
                if not existing or existing["body_sha256"] != sha or existing["source_system"] != source_system or existing["source_message_id"] != source_message_id:
                    raise ValueError("Message ID collision or source mismatch")
    return {"message_id": message_id, "sha256": sha, "completeness": completeness}


def update_document(
    logical_key: str, content_file: Path, expected_sha: str,
    task_id: UUID, agent_id: str, sections: list[str], message_refs: list[str],
) -> dict:
    content = content_file.read_text(encoding="utf-8")
    doc_id = document_id(logical_key)
    if parse_frontmatter_id(content) != doc_id:
        raise ValueError("Document ID cannot change with a file edit")
    new_sha = digest(content)
    with document_lock(doc_id):
        with connect() as database:
            try:
                with database.transaction():
                    row = database.execute(
                        "SELECT * FROM galactica_autonomy.documents WHERE id=%s FOR UPDATE",
                        (doc_id,),
                    ).fetchone()
                    if not row:
                        raise ValueError("Unknown Markdown document")
                    path = safe_path(row["relative_path"])
                    old_content = path.read_text(encoding="utf-8")
                    if file_digest(path) != expected_sha or row["sha256"] != expected_sha:
                        raise ValueError("Markdown version conflict; reload current content")
                    if new_sha == expected_sha:
                        return {"document_id": str(doc_id), "version": row["version"], "unchanged": True}
                    task = database.execute(
                        "SELECT id FROM galactica_autonomy.tasks WHERE id=%s", (task_id,)
                    ).fetchone()
                    if not task:
                        raise ValueError("Unknown task")
                    version = row["version"] + 1
                    atomic_write(path, content)
                    database.execute(
                        """UPDATE galactica_autonomy.documents
                           SET version=%s,sha256=%s,content=%s,updated_at=now()
                           WHERE id=%s""",
                        (version, new_sha, content, doc_id),
                    )
                    database.execute(
                        """INSERT INTO galactica_autonomy.document_versions
                           (document_id,version,sha256,content,task_id)
                           VALUES (%s,%s,%s,%s,%s)""",
                        (doc_id, version, new_sha, content, task_id),
                    )
                    database.execute(
                        """INSERT INTO galactica_autonomy.change_events
                           (task_id,agent_id,project_id,document_id,section_keys,
                            before_sha256,after_sha256,before_version,after_version,
                            source_message_ids,status)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'applied')""",
                        (task_id, agent_id, PROJECT_ID, doc_id, sections,
                         expected_sha, new_sha, row["version"], version, message_refs),
                    )
                return {"document_id": str(doc_id), "version": version, "sha256": new_sha}
            except Exception:
                if "path" in locals() and "old_content" in locals() and path.exists():
                    if digest(path.read_text(encoding="utf-8")) == new_sha:
                        atomic_write(path, old_content)
                raise


def finish_task(task_id: UUID, status: str, summary_file: Path) -> dict:
    summary = json.loads(summary_file.read_text(encoding="utf-8"))
    if not isinstance(summary, dict) or REQUIRED_SUMMARY - summary.keys():
        raise ValueError(f"Task summary missing fields: {sorted(REQUIRED_SUMMARY - summary.keys())}")
    serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    sha = digest(serialized)
    with connect() as database:
        with database.transaction():
            row = database.execute(
                "SELECT status,summary_sha256 FROM galactica_autonomy.tasks WHERE id=%s FOR UPDATE",
                (task_id,),
            ).fetchone()
            if not row:
                raise ValueError("Unknown task")
            if row["summary_sha256"]:
                if row["summary_sha256"] != sha or row["status"] != status:
                    raise ValueError("Task outcome is immutable; create a correction task")
            else:
                database.execute(
                    """UPDATE galactica_autonomy.tasks SET
                       summary=%s,summary_sha256=%s,status=%s,finished_at=now()
                       WHERE id=%s""",
                    (Jsonb(summary), sha, status, task_id),
                )
            database.execute(
                """INSERT INTO galactica_autonomy.outcome_protocol_jobs
                   (task_id,summary_sha256) VALUES (%s,%s)
                   ON CONFLICT (task_id) DO NOTHING""",
                (task_id, sha),
            )
    return {"task_id": str(task_id), "status": status,
            "summary_sha256": sha, "outcome_protocol": "queued"}


def reconcile() -> dict:
    changed: list[str] = []
    missing: list[str] = []
    with connect() as database:
        rows = database.execute("SELECT * FROM galactica_autonomy.documents").fetchall()
    by_id = {row["id"]: row for row in rows}
    locations: dict[UUID, Path] = {}
    for path in ROOT.rglob("*.md"):
        try:
            doc_id = parse_frontmatter_id(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if doc_id in locations:
            raise ValueError(f"Duplicate Markdown ID {doc_id}")
        locations[doc_id] = path
    for doc_id, row in by_id.items():
        path = locations.get(doc_id)
        if path is None:
            missing.append(row["relative_path"])
            continue
        relative = path.relative_to(ROOT).as_posix()
        with document_lock(doc_id):
            content = path.read_text(encoding="utf-8")
            raw_sha = file_digest(path)
            sha = digest(content)
            if sha == row["sha256"] and relative == row["relative_path"]:
                if raw_sha == sha:
                    continue
                atomic_write(path, content)
            with connect() as database:
                with database.transaction():
                    current = database.execute(
                        "SELECT * FROM galactica_autonomy.documents WHERE id=%s FOR UPDATE",
                        (doc_id,),
                    ).fetchone()
                    version = current["version"] + 1
                    database.execute(
                        """UPDATE galactica_autonomy.documents SET
                           relative_path=%s,version=%s,sha256=%s,content=%s,updated_at=now()
                           WHERE id=%s""",
                        (relative, version, sha, content, doc_id),
                    )
                    database.execute(
                        """INSERT INTO galactica_autonomy.document_versions
                           (document_id,version,sha256,content) VALUES (%s,%s,%s,%s)""",
                        (doc_id, version, sha, content),
                    )
                    database.execute(
                        """INSERT INTO galactica_autonomy.change_events
                           (agent_id,project_id,document_id,before_sha256,after_sha256,
                            before_version,after_version,status)
                           VALUES ('filesystem-reconciler',%s,%s,%s,%s,%s,%s,'reconciled')""",
                        (PROJECT_ID, doc_id, raw_sha, sha,
                         current["version"], version),
                    )
            changed.append(relative)
    return {"reconciled": changed, "missing": missing}


def normalize_registered_files() -> dict:
    changed = []
    with connect() as database:
        rows = database.execute("SELECT * FROM galactica_autonomy.documents").fetchall()
    for row in rows:
        with document_lock(row["id"]):
            path = safe_path(row["relative_path"])
            content = path.read_text(encoding="utf-8")
            if digest(content) != row["sha256"]:
                raise ValueError(f"Content changed outside store: {row['relative_path']}")
            raw_sha = file_digest(path)
            if raw_sha == row["sha256"]:
                continue
            atomic_write(path, content)
            with connect() as database:
                with database.transaction():
                    version = row["version"] + 1
                    database.execute(
                        "UPDATE galactica_autonomy.documents SET version=%s,updated_at=now() WHERE id=%s",
                        (version, row["id"]),
                    )
                    database.execute(
                        """INSERT INTO galactica_autonomy.document_versions
                           (document_id,version,sha256,content)
                           VALUES (%s,%s,%s,%s)""",
                        (row["id"], version, row["sha256"], content),
                    )
                    database.execute(
                        """INSERT INTO galactica_autonomy.change_events
                           (agent_id,project_id,document_id,section_keys,before_sha256,
                            after_sha256,before_version,after_version,status)
                           VALUES ('line-ending-migration',%s,%s,ARRAY['line_endings'],
                                   %s,%s,%s,%s,'reconciled')""",
                        (PROJECT_ID, row["id"], raw_sha, row["sha256"],
                         row["version"], version),
                    )
            changed.append(row["relative_path"])
    return {"normalized": changed}


def context(relative: str | None) -> str:
    base = ["GALACTICA.md", "AGENTS.md", "SOUL.md", "HARNESS.md", "USERPROFILE.md",
            "CLIENT/GALACTICA.md", "CLIENT/CURRENT_TASK.md"]
    if relative:
        parts = Path(relative).parts[:-1]
        for index in range(2, len(parts) + 1):
            base.append("/".join(parts[:index]) + "/GALACTICA.md")
    output = []
    for name in dict.fromkeys(base):
        path = safe_path(name)
        if path.exists():
            output.append(f"<!-- {name} -->\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("reconcile")
    commands.add_parser("normalize-newlines")
    load = commands.add_parser("context")
    load.add_argument("--path")
    start = commands.add_parser("start-task")
    start.add_argument("--task-id", type=UUID, required=True)
    start.add_argument("--agent-id", required=True)
    start.add_argument("--request-file", type=Path, required=True)
    start.add_argument("--criteria-file", type=Path, required=True)
    message = commands.add_parser("ingest-message")
    message.add_argument("--message-id", required=True)
    message.add_argument("--task-id", type=UUID)
    message.add_argument("--source-system", required=True)
    message.add_argument("--source-message-id", required=True)
    message.add_argument("--role", choices=["user", "assistant", "system", "tool"], required=True)
    message.add_argument("--text-file", type=Path, required=True)
    message.add_argument("--completeness", choices=["exact", "redacted", "partial"], required=True)
    message.add_argument("--source-locator")
    message.add_argument("--redaction-note")
    write = commands.add_parser("write-doc")
    write.add_argument("--logical-key", required=True)
    write.add_argument("--content-file", type=Path, required=True)
    write.add_argument("--expected-sha", required=True)
    write.add_argument("--task-id", type=UUID, required=True)
    write.add_argument("--agent-id", required=True)
    write.add_argument("--section", action="append", default=[])
    write.add_argument("--message-ref", action="append", default=[])
    finish = commands.add_parser("finish-task")
    finish.add_argument("--task-id", type=UUID, required=True)
    finish.add_argument("--status", choices=["succeeded", "failed", "partial"], required=True)
    finish.add_argument("--summary-file", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "init":
        result = seed_documents()
    elif args.command == "reconcile":
        result = reconcile()
    elif args.command == "normalize-newlines":
        result = normalize_registered_files()
    elif args.command == "context":
        print(context(args.path))
        return
    elif args.command == "start-task":
        result = start_task(args.task_id, args.agent_id, args.request_file, args.criteria_file)
    elif args.command == "ingest-message":
        result = ingest_message(args.message_id, args.task_id, args.source_system,
                                args.source_message_id, args.role, args.text_file,
                                args.completeness, args.source_locator, args.redaction_note)
    elif args.command == "write-doc":
        result = update_document(args.logical_key, args.content_file, args.expected_sha,
                                 args.task_id, args.agent_id, args.section, args.message_ref)
    else:
        result = finish_task(args.task_id, args.status, args.summary_file)
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
