# `.ai/` task workspace

This directory stores versioned task specifications, plans, implementation handoffs, cross-vendor reviews, resolutions, and verification evidence.

## Rules

- Stable project knowledge belongs in `docs/ai/`, not in task logs.
- Runtime transcripts, raw model outputs, large logs, and secrets do not belong in Git.
- Each task has one Writer and an independent Reviewer from the other vendor.
- A task is not complete until acceptance criteria and verification evidence are recorded.

## Create a task

```bash
aiwf-new-task <slug> <type> <R0|R1|R2|R3>
```

## Inspect or transition a task

```bash
aiwf-task-state TASK-YYYYMMDD-slug
aiwf-task-state TASK-YYYYMMDD-slug TRIAGED --actor human
aiwf-task-state TASK-YYYYMMDD-slug --resume --actor human --reason "blocker resolved"
```

Transitions are validated against the workflow state machine. Human gates and
all BLOCKED/CANCELLED/NEEDS_REDESIGN reasons are recorded in
`transitions.jsonl`.
