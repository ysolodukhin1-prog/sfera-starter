"""Evidence-gated skill candidates, promotion, versions, and rollback."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from uuid import UUID

from psycopg.types.json import Jsonb

from autonomy_store import connect, digest


ROOT = Path(os.getenv("AUTONOMY_SKILLS_ROOT", "/skill-artifacts"))
MIN_SUCCESSES = int(os.getenv("AUTONOMY_SKILL_MIN_SUCCESSES", "2"))


def check_key(key: str) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,63}", key):
        raise ValueError("Skill key must be a lowercase slug")


def register(spec: dict) -> dict:
    key = str(spec["key"])
    check_key(key)
    required = {"title", "scope", "tags", "steps", "prerequisites", "tools"}
    if required - spec.keys() or not isinstance(spec["steps"], list) or not spec["steps"]:
        raise ValueError("Skill candidate needs scope, steps, prerequisites and tools")
    procedure = {"steps": spec["steps"], "limitations": spec.get("limitations", [])}
    with connect() as db:
        with db.transaction():
            existing = db.execute(
                "SELECT * FROM galactica_autonomy.skill_candidates WHERE key=%s FOR UPDATE", (key,)
            ).fetchone()
            if existing:
                expected = (existing["title"] == spec["title"] and
                            existing["scope"] == spec["scope"] and
                            existing["tags"] == spec["tags"] and
                            existing["procedure"] == procedure and
                            existing["prerequisites"] == spec["prerequisites"] and
                            existing["tools"] == spec["tools"])
                if not expected:
                    raise ValueError("Candidate exists with different content; revise explicitly")
                return {"key": key, "version": existing["version"], "status": existing["status"]}
            db.execute(
                """INSERT INTO galactica_autonomy.skill_candidates
                   (key,title,scope,tags,procedure,prerequisites,tools)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (key, spec["title"], spec["scope"], spec["tags"],
                 Jsonb(procedure), Jsonb(spec["prerequisites"]), spec["tools"]),
            )
            db.execute(
                """INSERT INTO galactica_autonomy.skill_versions(candidate_key,version,snapshot,status)
                   VALUES (%s,1,%s,'candidate')""", (key, Jsonb(spec)),
            )
    return {"key": key, "version": 1, "status": "candidate"}


def record(key: str, task_id: UUID, succeeded: bool, test_refs: list[str]) -> dict:
    check_key(key)
    with connect() as db:
        with db.transaction():
            task = db.execute("SELECT status,summary FROM galactica_autonomy.tasks WHERE id=%s",
                              (task_id,)).fetchone()
            if not task or task["status"] == "active":
                raise ValueError("Application task must be finished")
            if succeeded and task["status"] != "succeeded":
                raise ValueError("Successful application requires a successful task")
            allowed = {str(c.get("source_ref")) for c in (task["summary"] or {}).get("checks", [])
                       if isinstance(c, dict) and c.get("source_ref")}
            if succeeded and (not test_refs or not set(test_refs).issubset(allowed)):
                raise ValueError("Successful application needs cited task checks")
            row = db.execute(
                """INSERT INTO galactica_autonomy.skill_applications
                   (candidate_key,task_id,succeeded,test_refs) VALUES (%s,%s,%s,%s)
                   ON CONFLICT (candidate_key,task_id) DO NOTHING RETURNING task_id""",
                (key, task_id, succeeded, test_refs),
            ).fetchone()
            if not row:
                prior = db.execute(
                    """SELECT succeeded,test_refs FROM galactica_autonomy.skill_applications
                       WHERE candidate_key=%s AND task_id=%s""", (key, task_id)
                ).fetchone()
                if prior["succeeded"] != succeeded or prior["test_refs"] != test_refs:
                    raise ValueError("Application outcome is immutable")
            counts = db.execute(
                """SELECT count(DISTINCT task_id) FILTER (WHERE succeeded) AS successes,
                          count(DISTINCT task_id) FILTER (WHERE NOT succeeded) AS failures
                   FROM galactica_autonomy.skill_applications WHERE candidate_key=%s""", (key,)
            ).fetchone()
    return {"key": key, "task_id": str(task_id), **counts}


def render(spec: dict, evidence: list[dict], version: int) -> str:
    lines = ["---", f"name: {spec['key']}", f"version: {version}",
             "---", "", f"# {spec['title']}", "", f"Scope: {spec['scope']}", "",
             "## Preconditions", ""]
    lines += [f"- {value}" for value in spec["prerequisites"]]
    lines += ["", "## Procedure", ""]
    lines += [f"{i}. {step}" for i, step in enumerate(spec["steps"], 1)]
    lines += ["", "## Tools", ""]
    lines += [f"- {value}" for value in spec["tools"]]
    lines += ["", "## Verified applications", ""]
    lines += [f"- Task {row['task_id']}: {', '.join(row['test_refs'])}" for row in evidence]
    lines += ["", "## Limits", ""]
    lines += [f"- {value}" for value in spec.get("limitations", [])]
    return "\n".join(lines).rstrip() + "\n"


def promote(key: str) -> dict:
    check_key(key)
    with connect() as db:
        with db.transaction():
            candidate = db.execute(
                "SELECT * FROM galactica_autonomy.skill_candidates WHERE key=%s FOR UPDATE", (key,)
            ).fetchone()
            if not candidate:
                raise ValueError("Unknown candidate")
            evidence = db.execute(
                """SELECT task_id,test_refs FROM galactica_autonomy.skill_applications
                   WHERE candidate_key=%s AND succeeded ORDER BY applied_at""", (key,)
            ).fetchall()
            if len({r["task_id"] for r in evidence}) < MIN_SUCCESSES:
                raise ValueError(f"Need {MIN_SUCCESSES} distinct successful tasks")
            if candidate["status"] == "active":
                return {"key": key, "status": "active", "version": candidate["version"]}
            version = candidate["version"] + 1
            spec = {"key": key, "title": candidate["title"], "scope": candidate["scope"],
                    "tags": candidate["tags"], "steps": candidate["procedure"]["steps"],
                    "limitations": candidate["procedure"].get("limitations", []),
                    "prerequisites": candidate["prerequisites"], "tools": candidate["tools"]}
            skill_text = render(spec, evidence, version)
            snapshot = {**spec, "evidence": [{"task_id": str(r["task_id"]),
                                              "test_refs": r["test_refs"]} for r in evidence],
                        "skill_md": skill_text, "sha256": digest(skill_text)}
            db.execute(
                """INSERT INTO galactica_autonomy.skill_versions
                   (candidate_key,version,snapshot,status) VALUES (%s,%s,%s,'active')""",
                (key, version, Jsonb(snapshot)),
            )
            db.execute(
                """UPDATE galactica_autonomy.skill_candidates SET status='active',
                   version=%s,updated_at=now() WHERE key=%s""", (version, key),
            )
    path = ROOT / key / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(skill_text, encoding="utf-8", newline="\n")
    return {"key": key, "status": "active", "version": version, "path": str(path)}


def restore(key: str, version: int) -> dict:
    check_key(key)
    with connect() as db:
        with db.transaction():
            old = db.execute(
                """SELECT snapshot FROM galactica_autonomy.skill_versions
                   WHERE candidate_key=%s AND version=%s""", (key, version)
            ).fetchone()
            candidate = db.execute(
                "SELECT version FROM galactica_autonomy.skill_candidates WHERE key=%s FOR UPDATE", (key,)
            ).fetchone()
            if not old or not candidate:
                raise ValueError("Unknown skill version")
            new_version = candidate["version"] + 1
            snapshot = dict(old["snapshot"])
            status = "active" if snapshot.get("skill_md") else "downgraded"
            if status == "active":
                snapshot["skill_md"] = render(snapshot, snapshot.get("evidence", []), new_version)
                snapshot["sha256"] = digest(snapshot["skill_md"])
            db.execute(
                """INSERT INTO galactica_autonomy.skill_versions
                   (candidate_key,version,snapshot,status) VALUES (%s,%s,%s,%s)""",
                (key, new_version, Jsonb(snapshot), status),
            )
            db.execute(
                """UPDATE galactica_autonomy.skill_candidates SET version=%s,status=%s,
                   updated_at=now() WHERE key=%s""", (new_version, status, key),
            )
    path = ROOT / key / "SKILL.md"
    if status == "active":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(snapshot["skill_md"], encoding="utf-8", newline="\n")
    else:
        path.unlink(missing_ok=True)
    return {"key": key, "status": status, "version": new_version,
            "restored_from_version": version}


def promote_ready() -> dict:
    with connect() as db:
        rows = db.execute(
            """SELECT c.key FROM galactica_autonomy.skill_candidates c
               JOIN galactica_autonomy.skill_applications a ON a.candidate_key=c.key
               WHERE c.status IN ('candidate','proposed') AND a.succeeded
               GROUP BY c.key HAVING count(DISTINCT a.task_id) >= %s""",
            (MIN_SUCCESSES,),
        ).fetchall()
    promoted = []
    for row in rows:
        promoted.append(promote(row["key"]))
    return {"promoted": promoted, "threshold": MIN_SUCCESSES}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("register")
    p.add_argument("--spec-file", type=Path, required=True)
    p = sub.add_parser("record")
    p.add_argument("--key", required=True)
    p.add_argument("--task-id", type=UUID, required=True)
    p.add_argument("--succeeded", choices=["true", "false"], required=True)
    p.add_argument("--test-ref", action="append", default=[])
    p = sub.add_parser("promote")
    p.add_argument("--key", required=True)
    p = sub.add_parser("restore")
    p.add_argument("--key", required=True)
    p.add_argument("--version", type=int, required=True)
    sub.add_parser("promote-ready")
    args = parser.parse_args()
    if args.command == "register":
        result = register(json.loads(args.spec_file.read_text(encoding="utf-8")))
    elif args.command == "record":
        result = record(args.key, args.task_id, args.succeeded == "true", args.test_ref)
    elif args.command == "promote":
        result = promote(args.key)
    elif args.command == "promote-ready":
        result = promote_ready()
    else:
        result = restore(args.key, args.version)
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
