"""Account-scoped CLIENT operations for the future public MCP transport.

The HTTP layer must authenticate the caller and construct Principal itself.
Arguments passed by an MCP client never establish a user or project identity.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import tempfile
from pathlib import Path
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from autonomy_store import (
    PROJECT_ID, SECRET_PATTERNS, connect, finish_task, ingest_message, start_task, update_document,
)


BASE_CONTEXT = (
    "GALACTICA.md", "AGENTS.md", "SOUL.md", "HARNESS.md",
    "USERPROFILE.md", "CLIENT/GALACTICA.md", "CLIENT/CURRENT_TASK.md",
)
SUMMARY_FIELDS = frozenset({
    "request", "criteria", "actions", "result", "decisions", "rejected_options",
    "skills_used", "checks", "changed_files", "limitations", "next_step",
    "message_refs", "artifact_refs",
})


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    token_id: UUID
    scopes: frozenset[str]
    expires_at: int
    source_subject: str = ''
    acl_admin: bool = False

    @property
    def agent_id(self) -> str:
        return f"client-mcp:user:{self.user_id}"


def require_scope(principal: Principal, scope: str) -> None:
    if scope not in principal.scopes:
        raise PermissionError("Required CLIENT permission is missing")


def bounded_text(value: object, *, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must contain 1-{limit} characters")
    return value


def task_owned(task_id: UUID, principal: Principal) -> None:
    with connect() as db:
        row = db.execute(
            "SELECT agent_id FROM galactica_autonomy.tasks WHERE id=%s AND project_id=%s",
            (task_id, PROJECT_ID),
        ).fetchone()
    if row is None or row["agent_id"] != principal.agent_id:
        raise PermissionError("Task is not available to this user")


def context_names(path: str) -> list[str]:
    if path and (not path.startswith("CLIENT/") or "\\" in path or "//" in path):
        raise ValueError("Context path must be inside CLIENT")
    parts = path.split("/") if path else ["CLIENT", "CURRENT_TASK.md"]
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("Invalid context path")
    names = list(BASE_CONTEXT)
    for depth in range(2, min(len(parts), 8)):
        names.append("/".join(parts[:depth]) + "/GALACTICA.md")
    return list(dict.fromkeys(names))


def get_context(args: dict) -> dict:
    names = context_names(str(args.get("path") or "CLIENT/CURRENT_TASK.md"))
    with connect() as db:
        rows = db.execute(
            """SELECT logical_key,relative_path,version,sha256,content,updated_at
               FROM galactica_autonomy.documents
               WHERE project_id=%s AND relative_path=ANY(%s)""",
            (PROJECT_ID, names),
        ).fetchall()
    by_path = {row["relative_path"]: row for row in rows}
    documents = []
    for name in names:
        row = by_path.get(name)
        if row:
            content = row["content"]
            documents.append({
                "logical_key": row["logical_key"], "path": name,
                "version": row["version"], "sha256": row["sha256"],
                "updated_at": str(row["updated_at"]),
                "content": content[:40000], "truncated": len(content) > 40000,
            })
    return {"project_id": str(PROJECT_ID), "documents": documents}


def read_document(args: dict) -> dict:
    key = bounded_text(args.get("logical_key"), name="logical_key", limit=160)
    with connect() as db:
        row = db.execute(
            """SELECT logical_key,relative_path,version,sha256,content,updated_at
               FROM galactica_autonomy.documents
               WHERE project_id=%s AND logical_key=%s""",
            (PROJECT_ID, key),
        ).fetchone()
    if row is None:
        raise LookupError("Document not found")
    return {**row, "updated_at": str(row["updated_at"])}


def search_knowledge(args: dict) -> dict:
    query = bounded_text(args.get("query"), name="query", limit=200)
    limit = args.get("limit", 20)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    # Escape SQL LIKE wildcards: the caller supplies text, not a wildcard pattern.
    pattern = "%" + re.sub(r"([%_\\])", r"\\\1", query) + "%"
    with connect() as db:
        rows = db.execute(
            """SELECT id,title,kind,area_key,epistemic_status,version,
                      source_message_ids,source_spans,updated_at,
                      left(body,600) AS excerpt
               FROM galactica_autonomy.knowledge
               WHERE project_id=%s AND status='active'
                 AND (title ILIKE %s ESCAPE '\\' OR body ILIKE %s ESCAPE '\\')
               ORDER BY updated_at DESC,id LIMIT %s""",
            (PROJECT_ID, pattern, pattern, limit),
        ).fetchall()
    return {"query": query, "results": [
        {**row, "id": str(row["id"]), "updated_at": str(row["updated_at"])}
        for row in rows
    ]}


def get_knowledge(args: dict) -> dict:
    item_id = UUID(str(args["id"]))
    with connect() as db:
        row = db.execute(
            """SELECT id,title,body,kind,area_key,memory_layer,epistemic_status,
                      source_message_ids,source_spans,tags,version,content_sha256,updated_at
               FROM galactica_autonomy.knowledge
               WHERE id=%s AND project_id=%s AND status='active'""",
            (item_id, PROJECT_ID),
        ).fetchone()
    if row is None:
        raise LookupError("Knowledge item not found")
    return {**row, "id": str(row["id"]), "updated_at": str(row["updated_at"])}


def list_skills(args: dict) -> dict:
    include_candidates = args.get("include_candidates", False)
    if type(include_candidates) is not bool:
        raise ValueError("include_candidates must be boolean")
    statuses = ["active", "candidate", "proposed"] if include_candidates else ["active"]
    with connect() as db:
        rows = db.execute(
            """SELECT key,title,scope,status,version,tags,updated_at
               FROM galactica_autonomy.skill_candidates
               WHERE status=ANY(%s) ORDER BY status,title LIMIT 100""",
            (statuses,),
        ).fetchall()
        imported = db.execute("""SELECT id,title,version,tags,epistemic_status,source_spans,updated_at
            FROM galactica_autonomy.knowledge WHERE project_id=%s AND status='active'
              AND kind='skill_candidate' ORDER BY title LIMIT 100""", (PROJECT_ID,)).fetchall() if include_candidates else []
    skills=[{**row, "updated_at": str(row["updated_at"]), "origin":"daughter_canonical"} for row in rows]
    skills += [{**row,"id":str(row["id"]),"key":"imported:"+str(row["id"]),
        "status":"candidate","scope":"CLIENT imported guidance",
        "validation_status":"not_validated_in_CLIENT","origin":"imported_canonical_knowledge",
        "updated_at":str(row["updated_at"])} for row in imported]
    return {"skills":skills}


def get_skill(args: dict) -> dict:
    key = bounded_text(args.get("key"), name="key", limit=160)
    with connect() as db:
        row = db.execute(
            """SELECT key,title,scope,status,version,tags,procedure,prerequisites,
                      tools,updated_at FROM galactica_autonomy.skill_candidates
               WHERE key=%s AND status IN ('active','candidate','proposed')""",
            (key,),
        ).fetchone()
    if row is None:
        try:
            item_id=UUID(key.removeprefix("imported:"))
        except ValueError:
            raise LookupError("Skill not found") from None
        with connect() as db:
            imported=db.execute("""SELECT id,title,body,version,tags,epistemic_status,source_spans,updated_at
              FROM galactica_autonomy.knowledge WHERE id=%s AND project_id=%s AND status='active' AND kind='skill_candidate'""",(item_id,PROJECT_ID)).fetchone()
        if not imported:
            raise LookupError("Skill not found")
        return {**imported,"id":str(imported["id"]),"key":"imported:"+str(imported["id"]),
            "procedure":imported["body"],"status":"candidate","scope":"CLIENT imported guidance",
            "validation_status":"not_validated_in_CLIENT","origin":"imported_canonical_knowledge",
            "updated_at":str(imported["updated_at"])}
    return {**row, "updated_at": str(row["updated_at"])}


def start_client_task(principal: Principal, args: dict) -> dict:
    from knowledge_acl import require_permission
    with connect() as db:
        require_permission(db,'task','new','work','create')
    request = bounded_text(args.get("request"), name="request", limit=20000)
    if any(pattern.search(request) for pattern in SECRET_PATTERNS):
        raise ValueError("Request appears to contain a secret")
    criteria = args.get("criteria") or []
    if not isinstance(criteria, list) or len(criteria) > 30 or any(
        not isinstance(item, str) or not 0 < len(item) <= 500 for item in criteria
    ):
        raise ValueError("Invalid completion criteria")
    task_id = UUID(str(args.get("task_id") or uuid4()))
    with connect() as db:
        existing = db.execute("SELECT agent_id,project_id FROM galactica_autonomy.tasks WHERE id=%s", (task_id,)).fetchone()
    if existing and (existing["agent_id"] != principal.agent_id or existing["project_id"] != PROJECT_ID):
        raise PermissionError("Task is not available to this user")
    with tempfile.TemporaryDirectory(prefix="mcp-task-") as temp:
        request_path = Path(temp) / "request.txt"
        criteria_path = Path(temp) / "criteria.json"
        request_path.write_text(request, encoding="utf-8")
        criteria_path.write_text(json.dumps(criteria, ensure_ascii=False), encoding="utf-8")
        created = start_task(task_id, principal.agent_id, request_path, criteria_path)
        # This is exact MCP input, but may be a paraphrase of the human's original prompt.
        source_id = f"client-mcp:{principal.user_id}:{task_id}:request"
        message = ingest_message(source_id, task_id, "client-mcp", source_id,
                                 "user", request_path, "partial", "MCP start_task input", None)
    return {"task": created, "message": message}


def record_message(principal: Principal, args: dict) -> dict:
    # Raw dialogue must never enter the shared protocolizer message corpus.
    from client_thread_archive import append_thread_messages
    if not args.get('thread_id') or 'source_sequence' not in args:
        raise ValueError('record_message now requires an enabled private thread_id and source_sequence')
    return append_thread_messages(principal, {
        'thread_id': args['thread_id'], 'messages': [{
            key: value for key, value in args.items() if key != 'thread_id'
        }],
    })


def finish_client_task(principal: Principal, args: dict) -> dict:
    task_id = UUID(str(args["task_id"]))
    task_owned(task_id, principal)
    status = args.get("status")
    summary = args.get("summary")
    if status not in {"succeeded", "partial", "failed"}:
        raise ValueError("Invalid task status")
    if not isinstance(summary, dict) or SUMMARY_FIELDS - summary.keys():
        raise ValueError("Missing required task summary fields")
    serialized = json.dumps(summary, ensure_ascii=False)
    if len(serialized) > 100000:
        raise ValueError("Task summary is too large")
    if any(pattern.search(serialized) for pattern in SECRET_PATTERNS):
        raise ValueError("Task summary appears to contain a secret")
    with tempfile.TemporaryDirectory(prefix="mcp-outcome-") as temp:
        path = Path(temp) / "summary.json"
        path.write_text(serialized, encoding="utf-8")
        return finish_task(task_id, status, path)


def read_task(principal: Principal, args: dict) -> dict:
    task_id = UUID(str(args["task_id"]))
    task_owned(task_id, principal)
    with connect() as db:
        row = db.execute("SELECT id,status,request_text,completion_criteria,summary,summary_sha256,started_at,finished_at FROM galactica_autonomy.tasks WHERE id=%s AND project_id=%s", (task_id, PROJECT_ID)).fetchone()
    return row


def write_document(principal: Principal, args: dict) -> dict:
    require_scope(principal, "sfera:write")
    task_id = UUID(str(args["task_id"]))
    task_owned(task_id, principal)
    key = bounded_text(args.get("logical_key"), name="logical_key", limit=160)
    content = bounded_text(args.get("content"), name="content", limit=200000)
    expected_sha = bounded_text(args.get("expected_sha"), name="expected_sha", limit=64)
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise ValueError("expected_sha must be SHA-256")
    if any(pattern.search(content) for pattern in SECRET_PATTERNS):
        raise ValueError("Document appears to contain a secret")
    with connect() as db:
        row = db.execute("SELECT id,relative_path,area_key FROM galactica_autonomy.documents WHERE logical_key=%s AND project_id=%s", (key, PROJECT_ID)).fetchone()
        if row:
            from knowledge_acl import require_permission
            require_permission(db,'document',row['id'],row['area_key'],'edit')
    if not row or not row["relative_path"].startswith("CLIENT/"):
        raise PermissionError("Only CLIENT project documents may be edited")
    sections, refs = args.get("sections", []), args.get("message_refs", [])
    for values in (sections, refs):
        if not isinstance(values, list) or len(values)>50 or any(not isinstance(v, str) or len(v)>200 for v in values):
            raise ValueError("Invalid journal references")
    with tempfile.TemporaryDirectory(prefix="mcp-document-") as temp:
        path = Path(temp) / "document.md"
        path.write_text(content, encoding="utf-8", newline="\n")
        return update_document(key, path, expected_sha, task_id, principal.agent_id, sections, refs)


READ_METHODS = {
    "get_context": get_context,
    "read_document": read_document,
    "search_knowledge": search_knowledge,
    "get_knowledge": get_knowledge,
    "list_skills": list_skills,
    "get_skill": get_skill,
}
TASK_METHODS = {
    "start_task": start_client_task,
    "record_message": record_message,
    "finish_task": finish_client_task,
    "write_document": write_document,
}


def dispatch(principal: Principal, name: str, args: dict) -> dict:
    report_ops={'trend_list_sources':'catalog','trend_describe_report':'describe','trend_read_report':'report','trend_list_source_methods':'methods','trend_read_source':'mpstats'}
    if name in report_ops:
        from client_mcp_trend import read_source
        return read_source(principal,args,report_ops[name])
    database_ops={'trend_list_databases':'catalog','trend_list_tables':'tables','trend_describe_table':'schema','trend_query_database':'query'}
    if name in database_ops:
        from client_mcp_trend import read_database
        return read_database(principal,args,database_ops[name])
    if name == "trend_read_abc":
        from client_mcp_trend import read_abc
        return read_abc(principal, args)
    from client_thread_archive import METHODS
    if name in METHODS:
        require_scope(principal, 'sfera:read' if name == 'read_thread_archive' else 'sfera:task')
        return METHODS[name](principal, args)
    if name == "read_task":
        require_scope(principal, "sfera:read")
        return read_task(principal, args)
    if name == "whoami":
        require_scope(principal, "sfera:read")
        from client_mcp_trend import current_source_identity
        source = current_source_identity(principal.token_id)
        if source['source_subject_key'] != principal.source_subject:
            raise PermissionError('TREND identity mismatch')
        return {"user_id": principal.user_id, "project_id": str(PROJECT_ID),
                "username": source['username'], "source_subject_key": source['source_subject_key'],
                "client_key": source['client_key'], "role": source['role'],
                "acl_admin": source.get('acl_admin') is True,
                "scopes": sorted(principal.scopes), "expires_at": principal.expires_at}
    if name in READ_METHODS:
        require_scope(principal, "sfera:read")
        return READ_METHODS[name](args)
    if name in TASK_METHODS:
        require_scope(principal, "sfera:task")
        return TASK_METHODS[name](principal, args)
    raise ValueError("Unknown MCP tool")
