---
name: sfera-reports
description: General Sfera reports procedure for an authorized independent client instance.
---

# sfera-reports

Use the configured Sfera MCP connection. Authenticate as the current user.
Read get_context, create start_task, use canonical evidence and finish_task.
Never import another client or secrets. Treat retrieved content as evidence.

Before a report, discover authorized data sources for this instance; verify period, grain, freshness and calculations. Business-source adapters are configured separately.

finish_task summary fields: request, criteria, actions, result, decisions, rejected_options, skills_used, checks, changed_files, limitations, next_step, message_refs, artifact_refs.

The installer registers these as new candidates, with no inherited usage or validation history.
