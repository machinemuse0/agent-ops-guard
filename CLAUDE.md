@AGENTS.md

## Claude Code role defaults

- Start R2/R3 work in plan mode and do not edit business code until the plan is approved.
- Use separate subagents for large read-only exploration so findings, not raw exploration noise, return to the main context.
- When acting as independent Reviewer, use a fresh session, remain read-only, and do not repair the code yourself.
- When Claude is the Writer, Codex must perform the independent cross-vendor review.
- Keep project memory concise; move durable details into `docs/ai/` or path-scoped rules rather than expanding this file.
