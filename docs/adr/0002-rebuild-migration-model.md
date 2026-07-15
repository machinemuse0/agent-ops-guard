# ADR 0002: Rebuild Migration Model

Status: accepted

## Decision

Schema changes that affect derived metadata use `python -m aicg rebuild --since
all`. Rebuild may clear and regenerate derived tables, but it must preserve
user-state tables such as `policy_acks`, `git_links`, `report_snapshots`, and
`alert_events`.

## Rationale

Old raw logs may be absent, partial, or privacy-sensitive. In-place semantic
migration of every derived value is less trustworthy than a bounded rebuild from
available local sources plus explicit preservation of user state.

## Consequences

Opening an older DB without explicit rebuild fails with an actionable message.
Opening a newer DB fails and asks the user to upgrade AgentOps Guard.
