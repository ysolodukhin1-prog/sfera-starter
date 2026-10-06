"""Single-call JSON bridge for the notebook MCP transport.

Runs inside the daughter container. All canonical operations stay on the VPS.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from uuid import UUID, uuid4

from autonomy_store import (
    ROOT, context, finish_task, ingest_message, reconcile, start_task,
    update_document,
)
from autonomy_import import accept, import_one


def staged(directory: Path, name: str, content: str) -> Path:
    path = directory / name
    path.write_text(content, encoding="utf-8", newline="\n")
    return path


def handle(name: str, args: dict):
    with tempfile.TemporaryDirectory(prefix="mcp-", dir=ROOT / ".incoming") as temp:
        directory = Path(temp)
        if name == "prepare_task":
            task_id = UUID(str(args.get("task_id") or uuid4()))
            source_id = str(args.get("source_message_id") or f"direct:{task_id}")
            request = staged(directory, "request.txt", str(args["request"]))
            criteria = staged(directory, "criteria.json", json.dumps(args.get("criteria") or [], ensure_ascii=False))
            task = start_task(task_id, "codex-notebook-direct", request, criteria)
            message = ingest_message(source_id, task_id, "codex-notebook", source_id,
                                     "user", request, "exact", None, None)
            return {"task": task, "message": message,
                    "context": context("CLIENT/CURRENT_TASK.md")}
        if name == "read_context":
            return context(str(args.get("path") or "CLIENT/CURRENT_TASK.md"))
        if name == "write_document":
            content = staged(directory, "content.md", str(args["content"]))
            return update_document(str(args["logical_key"]), content,
                                   str(args["expected_sha"]), UUID(str(args["task_id"])),
                                   "codex-notebook-direct", args.get("section_keys") or [],
                                   args.get("source_message_ids") or [])
        if name == "finish_task":
            summary = staged(directory, "summary.json", json.dumps(args["summary"], ensure_ascii=False))
            return finish_task(UUID(str(args["task_id"])), str(args["status"]), summary)
        if name == "ingest_message":
            body = staged(directory, "message.txt", str(args["body"]))
            return ingest_message(str(args["message_id"]), UUID(str(args["task_id"])),
                                  "codex-notebook", str(args["source_message_id"]),
                                  str(args["role"]), body, str(args.get("completeness", "exact")),
                                  args.get("source_locator"), args.get("redaction_note"))
        if name == "import_canonical":
            envelope = staged(directory, "import.json", json.dumps(args["envelope"], ensure_ascii=False))
            return import_one(envelope)
        if name == "accept_import":
            return accept(UUID(str(args["id"])))
        if name == "reconcile":
            return reconcile()
        raise ValueError("Unknown tool")


def main() -> None:
    try:
        request = json.load(sys.stdin)
        value = handle(str(request["name"]), request.get("arguments") or {})
        response = {"ok": True, "value": value}
    except Exception as error:
        response = {"ok": False, "error": {"type": type(error).__name__,
                                            "message": str(error)[:1000]}}
    print(json.dumps(response, ensure_ascii=False, default=str), flush=True)


if __name__ == "__main__":
    main()
