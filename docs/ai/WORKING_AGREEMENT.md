# Dual-Agent Working Agreement

## Roles

- Owner: defines goals, approves high-impact decisions, resolves disputes, merges and releases.
- Planner: normally Claude Code; explores and writes a verifiable plan.
- Writer: normally Codex; implements the approved scope in one branch/worktree.
- Reviewer: the other vendor model in a fresh, read-only context.
- Verifier: deterministic project commands and CI.

## Non-negotiable rules

1. One task, one Writer.
2. Two writing agents never share a checkout or overlapping file ownership.
3. Task files and Git are the handoff mechanism; chat history is not the source of truth.
4. The Writer may self-check but may not provide the independent final review.
5. Standard tasks receive one full cross-review/fix cycle; high-risk tasks receive at most two before redesign or human arbitration.
6. A model that fails the same fix twice hands a failure packet to the other model for a different root-cause hypothesis.
7. No agent may automatically push, merge, deploy, migrate production data, use secrets, or perform real financial/on-chain actions.

## Default pipeline

```text
Task → Claude plan → Codex plan challenge → Owner approval
→ Codex implementation → Claude review → Codex fix
→ quick/full verification → Owner merge
```

The Writer may be switched when project evidence shows another route is better. The Reviewer must still be the other vendor.

## WIP limit

For a single Owner:

- one task in implementation;
- one task in review/fix;
- one approved plan waiting to start.
