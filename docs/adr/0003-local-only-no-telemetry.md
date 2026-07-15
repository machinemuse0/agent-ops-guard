# ADR 0003: Local Only, No Telemetry

Status: accepted

## Decision

AgentOps Guard has no default network behavior, telemetry, auto-upload, billing
scraping, notification channel, or cloud dashboard.

## Rationale

The tool reads local agent logs that may contain sensitive prompts, code,
paths, secrets, and command output. Trust depends on the user being able to run
the tool offline and inspect every artifact before sharing it.

## Consequences

Feedback flows through explicit local artifacts such as `support bundle` and
redacted fixtures. The tool may help create those files, but the user performs
any sharing outside AgentOps Guard.
