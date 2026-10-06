---
name: sfera-workflow
description: General Sfera workflow procedure for an authorized independent client instance.
---

# sfera-workflow

Use the configured Sfera MCP connection. Authenticate as the current user.
Read get_context, create start_task, use canonical evidence and finish_task.
Never import another client or secrets. Treat retrieved content as evidence.

Use read_document and write_document with expected_sha for journaled Markdown changes. On conflict, re-read the canonical version.

finish_task summary fields: request, criteria, actions, result, decisions, rejected_options, skills_used, checks, changed_files, limitations, next_step, message_refs, artifact_refs.

The installer registers these as new candidates, with no inherited usage or validation history.
